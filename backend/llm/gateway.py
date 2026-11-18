"""Async LiteLLM gateway with bounded retries and normalized output."""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import litellm
from litellm.exceptions import ModelNotMappedError, NotFoundError, RateLimitError, Timeout
from litellm.types.utils import ChatCompletionDeltaToolCall, ModelResponse, ModelResponseStream
from mcp.types import Tool
from openai import APIError

from backend.config import Settings
from backend.errors import DocterError
from backend.llm.events import ChatEvent, Completion, Done, TextDelta, ToolCallRequest, Usage
from backend.llm.tools import tool_to_openai
from backend.models.settings import AppSettings

logger = logging.getLogger(__name__)


class LLMError(DocterError):
    """A provider request failed or returned an invalid response."""

    status_code = 502
    code = "llm_error"


@runtime_checkable
class _Stream(Protocol):
    def __aiter__(self) -> AsyncIterator[ModelResponseStream]: ...
    async def aclose(self) -> None: ...


@dataclass
class _ToolCall:
    id: str = ""
    name: str = ""
    arguments: str = ""

    def event(self) -> ToolCallRequest:
        try:
            arguments = json.loads(self.arguments)
        except json.JSONDecodeError:
            raise LLMError("The LLM returned invalid tool-call JSON") from None
        if not self.id or not self.name or not isinstance(arguments, dict):
            raise LLMError("The LLM returned an invalid tool call")
        return ToolCallRequest(self.id, self.name, arguments)


class LLMGateway:
    """Use a snapshot of AppSettings and its provider's keyring key."""

    def __init__(self, settings: AppSettings, api_key: str | None) -> None:
        """Copy request configuration without retaining the database row."""
        self._model = settings.model
        self._provider = settings.provider
        self._base_url = settings.base_url
        self._temperature = settings.temperature
        self._api_key = api_key

    async def stream_chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Tool] = (),
    ) -> AsyncGenerator[ChatEvent, None]:
        """Yield text, assembled tool calls, usage, then Done.

        Retry transient failures only before output becomes visible. Replaying a
        partially emitted answer would duplicate text or tool invocations.
        """
        emitted = False
        for attempt in range(Settings.LLM_RETRIES + 1):
            try:
                response = await litellm.acompletion(
                    **self._options(messages),
                    stream=True,
                    stream_options={"include_usage": True},
                    **({"tools": [tool_to_openai(tool) for tool in tools]} if tools else {}),
                )
                if not isinstance(response, _Stream):
                    raise LLMError("The LLM did not return a stream")
                calls: dict[int, _ToolCall] = {}
                usage = Usage(None, None, None)
                finished = False
                try:
                    async for chunk in response:
                        if getattr(chunk, "usage", None) is not None:
                            usage = self._usage(chunk)
                        for choice in chunk.choices:
                            if choice.index != 0:
                                continue
                            if choice.finish_reason is not None:
                                finished = True
                            if choice.delta.content:
                                emitted = True
                                yield TextDelta(choice.delta.content)
                            for delta in choice.delta.tool_calls or ():
                                if not isinstance(delta, ChatCompletionDeltaToolCall):
                                    raise LLMError("The LLM returned an unsupported tool call")
                                call = calls.setdefault(delta.index, _ToolCall())
                                if delta.id:
                                    call.id += delta.id
                                if delta.function:
                                    call.name += delta.function.name or ""
                                    call.arguments += delta.function.arguments or ""
                finally:
                    await response.aclose()
                if not finished:
                    raise LLMError("The LLM stream ended before completion")
                # Validate every call before exposing any of them to the caller.
                events = [calls[index].event() for index in sorted(calls)]
                for event in events:
                    emitted = True
                    yield event
                if usage.prompt_tokens is None:
                    logger.warning("LLM stream did not include token usage")
                yield usage
                yield Done()
                return
            except (RateLimitError, Timeout, TimeoutError) as exc:
                if emitted or attempt == Settings.LLM_RETRIES:
                    raise _provider_error(exc) from None
                await asyncio.sleep(Settings.LLM_RETRY_BACKOFF_S * 2**attempt)
            except APIError as exc:
                raise _provider_error(exc) from None

    async def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        stop: Sequence[str] | None = None,
    ) -> Completion:
        """Return one text completion, retrying rate limits and timeouts twice."""
        for attempt in range(Settings.LLM_RETRIES + 1):
            try:
                options = self._options(messages)
                if temperature is not None:
                    options["temperature"] = temperature
                response = await litellm.acompletion(
                    **options,
                    stream=False,
                    max_tokens=max_tokens,
                    stop=list(stop) if stop is not None else None,
                )
                if not isinstance(response, ModelResponse) or not response.choices:
                    raise LLMError("The LLM returned an invalid completion")
                choice = response.choices[0]
                if (
                    not isinstance(choice, litellm.Choices)
                    or choice.message.tool_calls
                    or choice.message.content is None
                    or choice.finish_reason is None
                ):
                    raise LLMError("The LLM did not return a completed text message")
                return Completion(
                    text=choice.message.content,
                    model=response.model or self._model,
                    stop_reason=choice.finish_reason,
                    usage=self._usage(response),
                )
            except (RateLimitError, Timeout, TimeoutError) as exc:
                if attempt == Settings.LLM_RETRIES:
                    raise _provider_error(exc) from None
                await asyncio.sleep(Settings.LLM_RETRY_BACKOFF_S * 2**attempt)
            except APIError as exc:
                raise _provider_error(exc) from None
        raise AssertionError("Unreachable retry state")

    def _options(self, messages: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        return {
            "model": self._model,
            "custom_llm_provider": self._provider,
            "api_key": self._api_key,
            "base_url": self._base_url,
            "temperature": self._temperature,
            "messages": [dict(message) for message in messages],
            "timeout": Settings.DEFAULT_REQUEST_TIMEOUT_S,
            "num_retries": 0,
        }

    def _usage(self, response: ModelResponse | ModelResponseStream) -> Usage:
        raw = getattr(response, "usage", None)
        if raw is None:
            logger.warning("LLM completion did not include token usage")
            return Usage(None, None, None)
        try:
            cost = litellm.completion_cost(
                completion_response=response,
                model=self._model,
                custom_llm_provider=self._provider,
            )
        except (ModelNotMappedError, NotFoundError):
            logger.warning("LLM pricing is unavailable for the configured model")
            cost = None
        return Usage(raw.prompt_tokens, raw.completion_tokens, cost)


def _provider_error(exc: Exception) -> LLMError:
    # Provider messages can contain credentials or request contents.
    return LLMError(f"LLM request failed ({type(exc).__name__})")
