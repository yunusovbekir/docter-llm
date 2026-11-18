import asyncio
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Any
from unittest.mock import AsyncMock

import anyio
import litellm
import pytest
from litellm.exceptions import AuthenticationError, RateLimitError, Timeout
from litellm.types.utils import Delta, ModelResponse, ModelResponseStream, StreamingChoices
from mcp.types import Tool

from backend.llm import (
    Done,
    LLMError,
    LLMGateway,
    TextDelta,
    ToolCallRequest,
    Usage,
    tool_to_openai,
)
from backend.models.settings import AppSettings


class FakeStream:
    def __init__(self, chunks: list[ModelResponseStream], error: Exception | None = None) -> None:
        self.chunks = chunks
        self.error = error
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[ModelResponseStream]:
        for chunk in self.chunks:
            yield chunk
        if self.error is not None:
            raise self.error

    async def aclose(self) -> None:
        self.closed = True


async def test_anyio_cancellation_waits_for_transport_close(
    gateway: LLMGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    reading = asyncio.Event()

    class BlockingStream(FakeStream):
        async def __aiter__(self) -> AsyncIterator[ModelResponseStream]:
            yield chunk({"content": "Partial"})
            reading.set()
            await asyncio.Event().wait()

        async def aclose(self) -> None:
            await anyio.lowlevel.checkpoint()
            self.closed = True

    stream = BlockingStream([])
    monkeypatch.setattr(litellm, "acompletion", AsyncMock(return_value=stream))
    scope = anyio.CancelScope()

    async def consume() -> None:
        with scope:
            async with aclosing(gateway.stream_chat([])) as events:
                async for _ in events:
                    pass

    task = asyncio.create_task(consume())
    try:
        async with asyncio.timeout(10):
            await reading.wait()
            scope.cancel()
            await task
        assert stream.closed
    finally:
        scope.cancel()
        await task


def chunk(
    delta: dict[str, Any] | None = None, finish: str | None = None, *, usage: bool = False
) -> ModelResponseStream:
    result = ModelResponseStream(
        model="gpt-4o-mini",
        choices=[]
        if usage
        else [StreamingChoices(index=0, delta=Delta(**(delta or {})), finish_reason=finish)],
    )
    if usage:
        result.usage = litellm.Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    return result


def response(finish: str = "stop") -> ModelResponse:
    return ModelResponse(
        model="gpt-4o-mini",
        choices=[{"message": {"role": "assistant", "content": "Hello"}, "finish_reason": finish}],
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    )


@pytest.fixture
def gateway() -> LLMGateway:
    return LLMGateway(
        AppSettings(base_url="https://provider.example/v1", temperature=0.4), "test-key"
    )


async def test_stream_assembles_interleaved_split_calls(
    gateway: LLMGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    stream = FakeStream(
        [
            chunk({"role": "assistant"}),
            chunk({"content": "Hello "}),
            chunk({"content": "world"}),
            chunk(
                {
                    "tool_calls": [
                        {
                            "index": 1,
                            "id": "call-b",
                            "function": {"name": "other", "arguments": '{"x":'},
                        },
                        {
                            "index": 0,
                            "id": "call-a",
                            "function": {"name": "ec", "arguments": '{"te'},
                        },
                    ]
                }
            ),
            chunk(
                {
                    "tool_calls": [
                        {"index": 0, "function": {"name": "ho", "arguments": 'xt":"a\\"b"}'}},
                        {"index": 1, "function": {"arguments": "2}"}},
                    ]
                }
            ),
            chunk(finish="tool_calls"),
            chunk(usage=True),
        ]
    )
    complete = AsyncMock(return_value=stream)
    monkeypatch.setattr(litellm, "acompletion", complete)
    tool = Tool(name="echo", input_schema={"type": "object"})

    events = [
        event async for event in gateway.stream_chat([{"role": "user", "content": "hi"}], [tool])
    ]

    assert events[:4] == [
        TextDelta("Hello "),
        TextDelta("world"),
        ToolCallRequest("call-a", "echo", {"text": 'a"b'}),
        ToolCallRequest("call-b", "other", {"x": 2}),
    ]
    assert isinstance(events[4], Usage)
    assert events[4].prompt_tokens == 10
    assert events[4].completion_tokens == 5
    assert events[4].cost_usd == pytest.approx(0.0000045)
    assert events[5] == Done()
    assert stream.closed
    options = complete.call_args.kwargs
    assert options["stream"] is True
    assert options["stream_options"] == {"include_usage": True}
    assert options["tools"] == [tool_to_openai(tool)]
    assert options["api_key"] == "test-key"
    assert options["base_url"] == "https://provider.example/v1"
    assert options["custom_llm_provider"] == "openai"
    assert options["temperature"] == 0.4
    assert options["num_retries"] == 0


@pytest.mark.parametrize("arguments", ['{"x":', "[]", "null", ""])
async def test_invalid_tool_arguments_fail_without_done(
    gateway: LLMGateway, monkeypatch: pytest.MonkeyPatch, arguments: str
) -> None:
    stream = FakeStream(
        [
            chunk(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call",
                            "function": {"name": "echo", "arguments": arguments},
                        }
                    ]
                }
            ),
            chunk(finish="tool_calls"),
        ]
    )
    monkeypatch.setattr(litellm, "acompletion", AsyncMock(return_value=stream))
    with pytest.raises(LLMError, match="tool"):
        _ = [event async for event in gateway.stream_chat([])]
    assert stream.closed


async def test_truncated_stream_is_not_success(
    gateway: LLMGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    stream = FakeStream([chunk({"content": "partial"})])
    monkeypatch.setattr(litellm, "acompletion", AsyncMock(return_value=stream))
    with pytest.raises(LLMError, match="before completion"):
        _ = [event async for event in gateway.stream_chat([])]
    assert stream.closed


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("kind", [RateLimitError, Timeout])
async def test_transient_errors_retry_twice_with_backoff(
    gateway: LLMGateway,
    monkeypatch: pytest.MonkeyPatch,
    streaming: bool,
    kind: type[RateLimitError] | type[Timeout],
) -> None:
    error = kind(message="secret test-key", model="model", llm_provider="openai")
    success = FakeStream([chunk(finish="stop"), chunk(usage=True)]) if streaming else response()
    complete = AsyncMock(side_effect=[error, error, success])
    sleep = AsyncMock()
    monkeypatch.setattr(litellm, "acompletion", complete)
    monkeypatch.setattr(asyncio, "sleep", sleep)

    if streaming:
        assert isinstance([event async for event in gateway.stream_chat([])][-1], Done)
    else:
        assert (await gateway.complete([])).text == "Hello"

    assert complete.call_count == 3
    assert [call.args[0] for call in sleep.call_args_list] == [1, 2]


@pytest.mark.parametrize("streaming", [False, True])
async def test_exhausted_retries_hide_provider_secrets(
    gateway: LLMGateway, monkeypatch: pytest.MonkeyPatch, streaming: bool
) -> None:
    error = RateLimitError(message="secret test-key", model="model", llm_provider="openai")
    complete = AsyncMock(side_effect=error)
    monkeypatch.setattr(litellm, "acompletion", complete)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    with pytest.raises(LLMError) as caught:
        if streaming:
            _ = [event async for event in gateway.stream_chat([])]
        else:
            await gateway.complete([])
    assert complete.call_count == 3
    assert "test-key" not in str(caught.value)
    assert caught.value.code == "llm_error"


async def test_partial_stream_is_not_replayed(
    gateway: LLMGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    stream = FakeStream(
        [chunk({"content": "partial"})],
        Timeout(message="test-key", model="model", llm_provider="openai"),
    )
    complete = AsyncMock(return_value=stream)
    monkeypatch.setattr(litellm, "acompletion", complete)
    events = []
    with pytest.raises(LLMError):
        async for event in gateway.stream_chat([]):
            events.append(event)
    assert events == [TextDelta("partial")]
    assert complete.call_count == 1
    assert stream.closed


async def test_stream_failure_before_text_can_retry(
    gateway: LLMGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = FakeStream([], Timeout(message="timeout", model="model", llm_provider="openai"))
    second = FakeStream([chunk(finish="stop"), chunk(usage=True)])
    complete = AsyncMock(side_effect=[first, second])
    monkeypatch.setattr(litellm, "acompletion", complete)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    assert isinstance([event async for event in gateway.stream_chat([])][-1], Done)
    assert first.closed and second.closed


@pytest.mark.parametrize("streaming", [False, True])
async def test_authentication_errors_are_not_retried(
    gateway: LLMGateway, monkeypatch: pytest.MonkeyPatch, streaming: bool
) -> None:
    complete = AsyncMock(
        side_effect=AuthenticationError(message="test-key", model="model", llm_provider="openai")
    )
    monkeypatch.setattr(litellm, "acompletion", complete)
    with pytest.raises(LLMError):
        if streaming:
            _ = [event async for event in gateway.stream_chat([])]
        else:
            await gateway.complete([])
    assert complete.call_count == 1


async def test_complete_forwards_sampling_options(
    gateway: LLMGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    complete = AsyncMock(return_value=response("length"))
    monkeypatch.setattr(litellm, "acompletion", complete)
    result = await gateway.complete(
        [{"role": "user", "content": "hi"}], max_tokens=8, temperature=0.8, stop=["END"]
    )
    assert result.text == "Hello"
    assert result.model == "gpt-4o-mini"
    assert result.stop_reason == "length"
    assert result.usage.prompt_tokens == 10
    options = complete.call_args.kwargs
    assert options["stream"] is False
    assert options["max_tokens"] == 8
    assert options["temperature"] == 0.8
    assert options["stop"] == ["END"]
    assert "tools" not in options


async def test_unknown_pricing_is_explicit(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    gateway = LLMGateway(AppSettings(model="private-model"), None)
    completion = response()
    completion.model = "private-model"
    monkeypatch.setattr(litellm, "acompletion", AsyncMock(return_value=completion))
    assert (await gateway.complete([])).usage == Usage(10, 5, None)
    assert "pricing is unavailable" in caplog.text


async def test_missing_usage_is_explicit(
    gateway: LLMGateway, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(
        litellm, "acompletion", AsyncMock(return_value=FakeStream([chunk(finish="stop")]))
    )
    assert [event async for event in gateway.stream_chat([])] == [Usage(None, None, None), Done()]
    assert "did not include token usage" in caplog.text


async def test_local_provider_needs_no_key_and_configuration_is_snapshotted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings(provider="ollama", model="llama3.1")
    gateway = LLMGateway(settings, None)
    settings.model = "changed"
    complete = AsyncMock(return_value=FakeStream([chunk(finish="stop")]))
    monkeypatch.setattr(litellm, "acompletion", complete)

    _ = [event async for event in gateway.stream_chat([])]

    options = complete.call_args.kwargs
    assert options["model"] == "llama3.1"
    assert options["custom_llm_provider"] == "ollama"
    assert options["api_key"] is None
    assert "tools" not in options


async def test_closing_generator_closes_provider_stream(
    gateway: LLMGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    stream = FakeStream([chunk({"content": "first"}), chunk(finish="stop")])
    monkeypatch.setattr(litellm, "acompletion", AsyncMock(return_value=stream))
    events = gateway.stream_chat([])
    assert await anext(events) == TextDelta("first")
    await events.aclose()
    assert stream.closed


async def test_cancellation_is_not_retried(
    gateway: LLMGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    complete = AsyncMock(side_effect=asyncio.CancelledError)
    monkeypatch.setattr(litellm, "acompletion", complete)
    with pytest.raises(asyncio.CancelledError):
        await gateway.complete([])
    assert complete.call_count == 1


def test_tool_converter_preserves_schema_without_aliasing() -> None:
    tool = Tool(
        name="echo",
        description="Echo text",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
    )
    converted = tool_to_openai(tool)
    assert converted == {
        "type": "function",
        "function": {
            "name": "echo",
            "description": "Echo text",
            "parameters": tool.input_schema,
        },
    }
    converted["function"]["parameters"]["properties"].clear()
    assert "text" in tool.input_schema["properties"]
