import json
import shlex
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import litellm
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from mcp import types
from mcp.client.session import ClientRequestContext
from mcp.types.jsonrpc import INTERNAL_ERROR, INVALID_PARAMS

from backend.config import Settings
from backend.llm import Completion, LLMError, LLMGateway, Usage
from backend.llm.sampling import SamplingHandler
from backend.main import create_app
from backend.mcp_client import MCPManager
from backend.models.settings import AppSettings
from backend.tests.llm.test_gateway import response


@pytest.mark.parametrize(
    ("finish", "expected"),
    [("stop", "endTurn"), ("length", "maxTokens"), ("stop_sequence", "stopSequence")],
)
async def test_sampling_maps_text_and_limits(
    monkeypatch: pytest.MonkeyPatch, finish: str, expected: str
) -> None:
    gateway = LLMGateway(AppSettings(), None)
    complete = AsyncMock(
        return_value=Completion("answer", "actual-model", finish, Usage(10, 5, 0.1))
    )
    monkeypatch.setattr(gateway, "complete", complete)
    handler = SamplingHandler(AsyncMock(return_value=gateway))
    result = await handler(
        Mock(spec=ClientRequestContext),
        types.CreateMessageRequestParams(
            messages=[
                types.SamplingMessage(
                    role="user",
                    content=[
                        types.TextContent(type="text", text="one"),
                        types.TextContent(type="text", text="two"),
                    ],
                )
            ],
            system_prompt="system",
            max_tokens=5,
            temperature=0.9,
            stop_sequences=["STOP"],
        ),
    )
    assert isinstance(result, types.CreateMessageResult)
    assert result.role == "assistant"
    assert result.content == types.TextContent(type="text", text="answer")
    assert result.model == "actual-model"
    assert result.stop_reason == expected
    complete.assert_awaited_once_with(
        [{"role": "system", "content": "system"}, {"role": "user", "content": "one\ntwo"}],
        max_tokens=5,
        temperature=0.9,
        stop=["STOP"],
    )


@pytest.mark.parametrize("unsupported", ["image", "tools", "limit"])
async def test_sampling_rejects_unsupported_input(unsupported: str) -> None:
    factory = AsyncMock()
    params = types.CreateMessageRequestParams(
        messages=[
            types.SamplingMessage(role="user", content=types.TextContent(type="text", text="hi"))
        ],
        max_tokens=10,
    )
    if unsupported == "image":
        params.messages[0].content = types.ImageContent(
            type="image", data="AA==", mime_type="image/png"
        )
    elif unsupported == "tools":
        params.tools = [types.Tool(name="echo", input_schema={"type": "object"})]
    else:
        params.max_tokens = 0
    result = await SamplingHandler(factory)(Mock(spec=ClientRequestContext), params)
    assert isinstance(result, types.ErrorData)
    assert result.code == INVALID_PARAMS
    factory.assert_not_awaited()


async def test_sampling_returns_protocol_error(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = LLMGateway(AppSettings(), None)
    monkeypatch.setattr(gateway, "complete", AsyncMock(side_effect=LLMError("Provider failed")))
    result = await SamplingHandler(AsyncMock(return_value=gateway))(
        Mock(spec=ClientRequestContext),
        types.CreateMessageRequestParams(messages=[], max_tokens=5),
    )
    assert isinstance(result, types.ErrorData)
    assert result.code == INTERNAL_ERROR
    assert result.message == "Provider failed"


async def test_sampling_round_trip_uses_current_saved_settings(
    data_dir: Path,
    frontend_dist_dir: Path,
    stub_mcp_command: list[str],
    clean_log_handlers: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        data_dir=data_dir,
        frontend_dist_dir=frontend_dist_dir,
        mcp_command=shlex.join([*stub_mcp_command, "--sampling"]),
    )
    app: FastAPI = create_app(settings)
    complete = AsyncMock(return_value=response())
    monkeypatch.setattr(litellm, "acompletion", complete)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        manager: MCPManager = app.state.mcp
        assert not manager.is_running
        for model, api_key in [("gpt-4o-mini", "first-key"), ("gpt-4o", "second-key")]:
            saved = await client.put(
                "/api/settings",
                json={
                    "provider": "openai",
                    "model": model,
                    "api_key": api_key,
                    "base_url": "https://provider.example/v1",
                },
            )
            assert saved.status_code == 200
            result = await manager.call_tool("sample", {}, timeout=10)
            assert not result.is_error, result.text
            sampled = json.loads(result.text)
            assert sampled["content"] == {"type": "text", "text": "Hello"}
            assert sampled["role"] == "assistant"
            assert sampled["model"] == "gpt-4o-mini"
            assert sampled["stopReason"] == "endTurn"
            options = complete.call_args.kwargs
            assert options["model"] == model
            assert options["api_key"] == api_key
            assert options["base_url"] == "https://provider.example/v1"
            assert options["messages"] == [
                {"role": "system", "content": "Be concise"},
                {"role": "user", "content": "Say hello"},
                {"role": "assistant", "content": "Hi"},
                {"role": "user", "content": "Again"},
            ]
            assert options["max_tokens"] == 16
            assert options["temperature"] == 0.6
            assert options["stop"] == ["END"]
    assert not manager.is_running
