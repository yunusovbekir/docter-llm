"""MCP sampling through the user's configured LLM provider."""

from collections.abc import Awaitable, Callable
from typing import Any

from mcp import types
from mcp.client.session import ClientRequestContext
from mcp.types.jsonrpc import INTERNAL_ERROR, INVALID_PARAMS

from backend.llm.gateway import LLMError, LLMGateway


class SamplingHandler:
    """Create a fresh gateway for each server-initiated text sampling request."""

    def __init__(self, gateway_factory: Callable[[], Awaitable[LLMGateway]]) -> None:
        """Use a factory so saved settings and keyring changes take effect immediately."""
        self._gateway_factory = gateway_factory

    async def __call__(
        self,
        context: ClientRequestContext,
        params: types.CreateMessageRequestParams,
    ) -> types.CreateMessageResult | types.ErrorData:
        """Translate MCP text messages and generation limits into a completion."""
        if params.tools or params.tool_choice is not None:
            return types.ErrorData(
                code=INVALID_PARAMS, message="Sampling supports text completions, not tools"
            )
        if params.max_tokens <= 0:
            return types.ErrorData(code=INVALID_PARAMS, message="maxTokens must be positive")
        messages: list[dict[str, Any]] = []
        if params.system_prompt is not None:
            messages.append({"role": "system", "content": params.system_prompt})
        for message in params.messages:
            blocks = message.content if isinstance(message.content, list) else [message.content]
            if not all(isinstance(block, types.TextContent) for block in blocks):
                return types.ErrorData(
                    code=INVALID_PARAMS, message="Sampling supports only text content"
                )
            messages.append(
                {
                    "role": message.role,
                    "content": "\n".join(
                        block.text for block in blocks if isinstance(block, types.TextContent)
                    ),
                }
            )
        try:
            gateway = await self._gateway_factory()
            result = await gateway.complete(
                messages,
                max_tokens=params.max_tokens,
                temperature=params.temperature,
                stop=params.stop_sequences,
            )
        except LLMError as exc:
            return types.ErrorData(code=INTERNAL_ERROR, message=exc.message)
        return types.CreateMessageResult(
            role="assistant",
            content=types.TextContent(type="text", text=result.text),
            model=result.model,
            stop_reason={
                "stop": "endTurn",
                "length": "maxTokens",
                "stop_sequence": "stopSequence",
            }.get(result.stop_reason, result.stop_reason),
        )
