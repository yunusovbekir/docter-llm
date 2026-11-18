"""HTTP/SSE session lifecycle tests with scripted LLM and MCP responses."""

import asyncio
import json
import socket
from collections.abc import AsyncGenerator, AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
import uvicorn
from fastapi import FastAPI
from httpx import AsyncClient, Response
from mcp.types import Tool
from sqlmodel import select

from backend.config import Settings
from backend.llm import ChatEvent, Done, LLMError, TextDelta, ToolCallRequest
from backend.mcp_client import ToolResult
from backend.models import Project, Session
from backend.models.project import IndexStatus
from backend.models.session import RunStatus
from backend.services.sessions import ChatRun, SessionService
from backend.services.settings import SettingsService
from backend.tests.git_utils import GitRepoFactory


@dataclass
class Pause:
    reached: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    async def wait(self) -> None:
        self.reached.set()
        await self.release.wait()


class ScriptedLLM:
    def __init__(self) -> None:
        self.turns: list[Sequence[ChatEvent | Exception | Pause]] = []
        self.requests: list[list[Mapping[str, Any]]] = []
        self.closed = 0

    async def stream_chat(
        self, messages: Sequence[Mapping[str, Any]], tools: Sequence[Tool] = ()
    ) -> AsyncGenerator[ChatEvent, None]:
        self.requests.append(deepcopy(list(messages)))
        assert self.turns, "Unexpected LLM completion"
        try:
            for item in self.turns.pop(0):
                if isinstance(item, Exception):
                    raise item
                if isinstance(item, Pause):
                    await item.wait()
                else:
                    yield item
        finally:
            self.closed += 1


@pytest.fixture
def llm(app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> ScriptedLLM:
    llm = ScriptedLLM()
    monkeypatch.setattr(SettingsService, "gateway", AsyncMock(return_value=llm))
    monkeypatch.setattr(
        app.state.mcp,
        "list_tools",
        AsyncMock(
            return_value=[
                Tool(name="read_file", input_schema={}),
                Tool(name="propose_patch", input_schema={}),
            ]
        ),
    )
    monkeypatch.setattr(
        app.state.mcp, "call_tool", AsyncMock(return_value=ToolResult(text="File contents"))
    )
    return llm


async def _project(
    app: FastAPI, status: IndexStatus = IndexStatus.DONE, *, path: str = "/example"
) -> int:
    async with app.state.sessionmaker() as db:
        project = Project(name="example", path=path, index_status=status)
        db.add(project)
        await db.commit()
        assert project.id is not None
        return project.id


async def _session(client: AsyncClient, project_id: int) -> int:
    response = await client.post(
        "/api/sessions", json={"project_id": project_id, "mode": "add", "mode_params": {}}
    )
    assert response.status_code == 201, response.text
    session_id: int = response.json()["id"]
    return session_id


async def _events(response: Response) -> list[dict[str, Any]]:
    assert response.status_code == 200, await response.aread()
    assert response.headers["content-type"].startswith("text/event-stream")
    result: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    async for line in response.aiter_lines():
        if not line:
            if current:
                assert "id" in current
                assert current["event"] == current["data"]["type"]
                result.append(current)
                current = {}
        elif not line.startswith(":"):
            key, _, value = line.partition(":")
            current[key] = json.loads(value.lstrip()) if key == "data" else value.lstrip()
    assert not current
    ids = [event["id"] for event in result]
    assert len(set(ids)) == len(ids)
    assert [int(event_id.rsplit(":", 1)[1]) for event_id in ids] == list(range(1, len(ids) + 1))
    return result


@asynccontextmanager
async def _live_client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Use a real HTTP connection; ASGITransport buffers streaming responses."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(
            uvicorn.Config(app, lifespan="off", log_config=None, access_log=False)
        )
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    if task.done():
                        await task
                    await asyncio.sleep(0.01)
            async with AsyncClient(
                base_url=f"http://127.0.0.1:{sock.getsockname()[1]}", timeout=10
            ) as client:
                yield client
        finally:
            server.should_exit = True
            await task


async def _idle(client: AsyncClient, session_id: int) -> dict[str, Any]:
    async with asyncio.timeout(10):
        while True:
            response = await client.get(f"/api/sessions/{session_id}")
            assert response.status_code == 200
            body: dict[str, Any] = response.json()
            if body["run_status"] == "idle":
                return body
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("status", [IndexStatus.IDLE, IndexStatus.RUNNING, IndexStatus.FAILED])
async def test_creating_session_requires_completed_index(
    app: FastAPI, client: AsyncClient, status: IndexStatus
) -> None:
    project_id = await _project(app, status)

    response = await client.post("/api/sessions", json={"project_id": project_id, "mode": "add"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "project_not_indexed"
    async with app.state.sessionmaker() as db:
        assert (await db.exec(select(Session))).all() == []


async def test_create_and_get_session(app: FastAPI, client: AsyncClient) -> None:
    project_id = await _project(app)
    params = {"target_language": "ja", "paths": ["README.md"]}

    response = await client.post(
        "/api/sessions",
        json={"project_id": project_id, "mode": "translate", "mode_params": params},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["project_id"] == project_id
    assert body["mode"] == "translate"
    assert body["mode_params"] == params
    assert body["run_status"] == "idle"
    assert body["branch_name"] is None
    assert datetime.fromisoformat(body["created_at"]).tzinfo is not None
    assert datetime.fromisoformat(body["updated_at"]).tzinfo is not None
    detail = await client.get(f"/api/sessions/{body['id']}")
    assert detail.status_code == 200
    assert detail.json() == {**body, "messages": []}


@pytest.mark.parametrize(
    "fields",
    [
        {"mode": "unknown"},
        {"mode": "translate"},
        {"mode": "translate", "mode_params": {"target_language": "Japanese"}},
        {"mode_params": []},
        {"unexpected": True},
    ],
)
async def test_invalid_session_configuration(
    app: FastAPI, client: AsyncClient, fields: dict[str, Any]
) -> None:
    project_id = await _project(app)
    response = await client.post(
        "/api/sessions", json={"project_id": project_id, "mode": "add", **fields}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] in {"validation_error", "invalid_mode_params"}


async def test_unknown_project_and_session(client: AsyncClient) -> None:
    response = await client.post("/api/sessions", json={"project_id": 999, "mode": "add"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "project_not_found"
    for response in (
        await client.get("/api/sessions/999"),
        await client.get("/api/sessions/999/changes"),
        await client.post("/api/sessions/999/chat", json={"message": "Hello"}),
        await client.post("/api/sessions/999/cancel"),
    ):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "session_not_found"


async def test_chat_openapi_describes_sse(client: AsyncClient) -> None:
    schema = (await client.get("/openapi.json")).json()

    content = schema["paths"]["/api/sessions/{session_id}/chat"]["post"]["responses"]["200"][
        "content"
    ]
    assert set(content) == {"text/event-stream"}


@pytest.mark.parametrize("body", [{}, {"message": ""}, {"message": " \n\t"}, {"message": 3}])
async def test_chat_rejects_invalid_message(
    app: FastAPI, client: AsyncClient, body: dict[str, Any]
) -> None:
    session_id = await _session(client, await _project(app))
    response = await client.post(f"/api/sessions/{session_id}/chat", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert (await client.get(f"/api/sessions/{session_id}")).json()["messages"] == []


async def test_sse_persistence_and_conversation_history(
    app: FastAPI, client: AsyncClient, llm: ScriptedLLM
) -> None:
    session_id = await _session(client, await _project(app))
    url = f"/api/sessions/{session_id}"
    llm.turns = [
        [
            TextDelta("Reading"),
            ToolCallRequest("read-1", "read_file", {"file_path": "README.md"}),
            Done(),
        ],
        [TextDelta("All"), TextDelta(" done"), Done()],
        [TextDelta("Follow-up"), Done()],
    ]

    async with client.stream(
        "POST", f"{url}/chat", json={"message": "  Improve docs\n"}
    ) as response:
        streamed = await _events(response)

    assert [event["event"] for event in streamed] == [
        "token",
        "tool_call",
        "tool_result",
        "token",
        "token",
        "done",
    ]
    assert streamed[1]["data"]["id"] == "read-1"
    assert streamed[1]["id"] != "read-1"
    assert streamed[-1]["data"] == {"type": "done", "reason": "completed", "steps": 2}
    body = (await client.get(url)).json()
    assert body["run_status"] == "idle"
    messages = body["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant", "tool", "assistant"]
    assert [message["content"] for message in messages] == [
        "  Improve docs\n",
        "Reading",
        "File contents",
        "All done",
    ]
    calls = messages[1]["tool_calls"]
    assert calls == [
        {
            "id": "read-1",
            "type": "function",
            "function": {
                "name": "read_file",
                "arguments": json.dumps({"file_path": "README.md", "repo": "/example"}),
            },
        }
    ]
    assert messages[2]["tool_call_id"] == "read-1"
    assert len({message["id"] for message in messages}) == 4
    assert all(message["session_id"] == session_id for message in messages)
    assert all(datetime.fromisoformat(message["created_at"]).tzinfo for message in messages)

    async with client.stream("POST", f"{url}/chat", json={"message": "Next"}) as response:
        follow_up = await _events(response)

    assert {event["id"] for event in streamed}.isdisjoint(event["id"] for event in follow_up)
    assert llm.requests[2][1:] == [
        {"role": "user", "content": "  Improve docs\n"},
        {"role": "assistant", "content": "Reading", "tool_calls": calls},
        {"role": "tool", "content": "File contents", "tool_call_id": "read-1"},
        {"role": "assistant", "content": "All done"},
        {"role": "user", "content": "Next"},
    ]
    fresh = SessionService(app.state.sessionmaker, app.state.mcp)
    assert len((await fresh.get(session_id)).messages) == 6


async def test_messages_persist_at_completion_not_end_of_run(
    app: FastAPI, client: AsyncClient, llm: ScriptedLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = await _session(client, await _project(app))
    url = f"/api/sessions/{session_id}"
    tool_pause, text_pause = Pause(), Pause()

    async def call_tool(*args: object, **kwargs: object) -> ToolResult:
        await tool_pause.wait()
        return ToolResult(text="Read")

    monkeypatch.setattr(app.state.mcp, "call_tool", call_tool)
    llm.turns = [
        [TextDelta("Reading"), ToolCallRequest("read", "read_file", {}), Done()],
        [TextDelta("Unfinished"), text_pause, Done()],
    ]
    task = asyncio.create_task(client.post(f"{url}/chat", json={"message": "Start"}))
    try:
        async with asyncio.timeout(10):
            await tool_pause.reached.wait()
            body = (await client.get(url)).json()
            assert body["run_status"] == "running"
            assert [message["role"] for message in body["messages"]] == ["user", "assistant"]
            tool_pause.release.set()
            await text_pause.reached.wait()
            body = (await client.get(url)).json()
            assert [message["role"] for message in body["messages"]] == [
                "user",
                "assistant",
                "tool",
            ]
            text_pause.release.set()
            assert (await task).status_code == 200
        assert len((await client.get(url)).json()["messages"]) == 4
    finally:
        await client.post(f"{url}/cancel")
        await task


async def test_only_one_active_run_and_retry_after_completion(
    app: FastAPI, client: AsyncClient, llm: ScriptedLLM
) -> None:
    session_id = await _session(client, await _project(app))
    url = f"/api/sessions/{session_id}"
    pause = Pause()
    llm.turns = [[TextDelta("One"), pause, Done()], [TextDelta("Two"), Done()]]
    first = asyncio.create_task(client.post(f"{url}/chat", json={"message": "First"}))
    try:
        async with asyncio.timeout(10):
            await pause.reached.wait()
            rejected = await client.post(f"{url}/chat", json={"message": "Duplicate"})
            assert rejected.status_code == 409
            assert rejected.json()["error"]["code"] == "session_running"
            assert [
                message["content"] for message in (await client.get(url)).json()["messages"]
            ] == ["First"]
            pause.release.set()
            assert (await first).status_code == 200
        assert (await client.post(f"{url}/chat", json={"message": "Second"})).status_code == 200
    finally:
        await client.post(f"{url}/cancel")
        await first


async def test_cancel_is_idempotent_and_drops_incomplete_assistant(
    app: FastAPI, client: AsyncClient, llm: ScriptedLLM
) -> None:
    session_id = await _session(client, await _project(app))
    url = f"/api/sessions/{session_id}"
    pause = Pause()
    llm.turns = [[TextDelta("Partial"), pause, Done()], [TextDelta("Retry"), Done()]]
    assert (await client.post(f"{url}/cancel")).status_code == 204
    task = asyncio.create_task(client.post(f"{url}/chat", json={"message": "First"}))
    async with asyncio.timeout(10):
        await pause.reached.wait()
        responses = await asyncio.gather(client.post(f"{url}/cancel"), client.post(f"{url}/cancel"))
        assert [response.status_code for response in responses] == [204, 204]
        streamed = await _events(await task)

    assert [event["event"] for event in streamed] == ["token"]
    assert llm.closed == 1
    body = (await client.get(url)).json()
    assert body["run_status"] == "idle"
    assert [message["role"] for message in body["messages"]] == ["user"]
    assert (await client.post(f"{url}/chat", json={"message": "Again"})).status_code == 200
    assert len((await client.get(url)).json()["messages"]) == 3


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (LLMError("Provider unavailable"), "llm_error"),
        (RuntimeError("private details"), "internal_error"),
    ],
)
async def test_stream_errors_are_json_and_release_run(
    app: FastAPI,
    client: AsyncClient,
    llm: ScriptedLLM,
    failure: Exception,
    code: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    session_id = await _session(client, await _project(app))
    url = f"/api/sessions/{session_id}"
    llm.turns = [[TextDelta("Partial"), failure]]

    streamed = await _events(await client.post(f"{url}/chat", json={"message": "Start"}))

    assert [event["event"] for event in streamed] == ["token", "error", "done"]
    assert streamed[1]["data"]["code"] == code
    assert streamed[2]["data"]["reason"] == "error"
    assert "private details" not in json.dumps(streamed) + caplog.text
    body = (await client.get(url)).json()
    assert body["run_status"] == "idle"
    assert [message["role"] for message in body["messages"]] == ["user"]


async def test_saved_step_limit_and_proposal_events(
    app: FastAPI,
    client: AsyncClient,
    llm: ScriptedLLM,
    monkeypatch: pytest.MonkeyPatch,
    make_git_repo: GitRepoFactory,
) -> None:
    repo = make_git_repo()
    assert repo.working_tree_dir is not None
    session_id = await _session(client, await _project(app, path=str(repo.working_tree_dir)))
    assert (await client.put("/api/settings", json={"max_agent_steps": 1})).status_code == 200
    patch = {
        "file_path": "README.md",
        "patch": "--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-# Demo\n+# Docs\n",
        "rationale": "Fix heading",
        "is_new_file": False,
    }
    monkeypatch.setattr(
        app.state.mcp, "call_tool", AsyncMock(return_value=ToolResult(text="", structured=patch))
    )
    llm.turns = [[ToolCallRequest("patch", "propose_patch", patch), Done()]]

    streamed = await _events(
        await client.post(f"/api/sessions/{session_id}/chat", json={"message": "Improve"})
    )

    assert [event["event"] for event in streamed] == [
        "tool_call",
        "tool_result",
        "change_proposed",
        "warning",
        "done",
    ]
    assert streamed[2]["data"]["change"] == patch
    assert streamed[3]["data"]["code"] == "max_agent_steps"
    assert streamed[4]["data"] == {"type": "done", "reason": "max_steps", "steps": 1}
    body = (await client.get(f"/api/sessions/{session_id}")).json()
    assert [message["role"] for message in body["messages"]] == ["user", "assistant", "tool"]
    assert body["messages"][1]["content"] is None

    sets = (await client.get(f"/api/sessions/{session_id}/changes")).json()
    assert len(sets) == 1
    assert sets[0]["run_id"] == streamed[0]["id"].rsplit(":", 1)[0]
    assert sets[0]["session_id"] == session_id
    change = sets[0]["changes"][0]
    assert change["change_set_id"] == sets[0]["id"]
    assert change["patch"] == patch["patch"]
    assert change["original_content"] == "# Demo\n"
    assert change["proposed_content"] == "# Docs\n"
    assert change["hunks"][0]["header"] == "@@ -1 +1 @@"
    assert change["hunks"][0]["status"] == "pending"
    fresh = SessionService(app.state.sessionmaker, app.state.mcp)
    assert [item.model_dump(mode="json") for item in await fresh.get_changes(session_id)] == sets


async def test_changes_are_scoped_to_session_and_one_set_per_run(
    app: FastAPI, client: AsyncClient, llm: ScriptedLLM
) -> None:
    project_id = await _project(app)
    first = await _session(client, project_id)
    second = await _session(client, project_id)
    assert (await client.get(f"/api/sessions/{first}/changes")).json() == []
    llm.turns = [[Done()], [Done()], [Done()]]
    for session_id in (first, second, first):
        assert (
            await client.post(f"/api/sessions/{session_id}/chat", json={"message": "Hello"})
        ).status_code == 200

    first_sets = (await client.get(f"/api/sessions/{first}/changes")).json()
    second_sets = (await client.get(f"/api/sessions/{second}/changes")).json()
    assert len(first_sets) == 2
    assert len(second_sets) == 1
    assert len({item["run_id"] for item in first_sets + second_sets}) == 3
    assert all(item["session_id"] == first and item["changes"] == [] for item in first_sets)
    assert second_sets[0]["session_id"] == second


async def test_agent_retries_rejected_patch_and_only_persists_valid_proposal(
    app: FastAPI,
    client: AsyncClient,
    llm: ScriptedLLM,
    monkeypatch: pytest.MonkeyPatch,
    make_git_repo: GitRepoFactory,
) -> None:
    repo = make_git_repo()
    assert repo.working_tree_dir is not None
    session_id = await _session(client, await _project(app, path=str(repo.working_tree_dir)))
    patch = "--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-# Demo\n+# Docs\n"
    good = {
        "file_path": "README.md",
        "patch": patch,
        "rationale": "Fix heading",
        "is_new_file": False,
    }
    bad = {**good, "patch": patch.replace("-# Demo", "-stale")}
    monkeypatch.setattr(
        app.state.mcp,
        "call_tool",
        AsyncMock(
            side_effect=[
                ToolResult(text="", structured=bad),
                ToolResult(text="", structured=good),
            ]
        ),
    )
    llm.turns = [
        [ToolCallRequest("bad", "propose_patch", {}), Done()],
        [ToolCallRequest("retry", "propose_patch", {}), Done()],
        [Done()],
    ]

    streamed = await _events(
        await client.post(f"/api/sessions/{session_id}/chat", json={"message": "Improve"})
    )

    results = [item["data"] for item in streamed if item["event"] == "tool_result"]
    assert [item["is_error"] for item in results] == [True, False]
    assert "does not apply cleanly" in results[0]["text"]
    assert llm.requests[1][-1]["content"].startswith("Tool error:")
    assert len([item for item in streamed if item["event"] == "change_proposed"]) == 1
    sets = (await client.get(f"/api/sessions/{session_id}/changes")).json()
    assert len(sets) == 1
    assert len(sets[0]["changes"]) == 1
    assert sets[0]["changes"][0]["patch"] == good["patch"]


@pytest.mark.parametrize("read_token", [True, False])
async def test_real_http_disconnect_cancels_worker(
    app: FastAPI, client: AsyncClient, llm: ScriptedLLM, read_token: bool
) -> None:
    session_id = await _session(client, await _project(app))
    url = f"/api/sessions/{session_id}"
    pause = Pause()
    llm.turns = [[TextDelta("Partial"), pause, Done()], [TextDelta("Retry"), Done()]]

    async with _live_client(app) as live:
        async with live.stream("POST", f"{url}/chat", json={"message": "Start"}) as response:
            assert response.status_code == 200
            if read_token:
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        assert json.loads(line[5:]) == {"type": "token", "text": "Partial"}
                        break
            async with asyncio.timeout(10):
                await pause.reached.wait()
        body = await _idle(client, session_id)
        assert llm.closed == 1
        assert [message["role"] for message in body["messages"]] == ["user"]
        assert (await live.post(f"{url}/chat", json={"message": "Again"})).status_code == 200


async def test_shutdown_and_restart_clear_running_state(
    app: FastAPI, client: AsyncClient, llm: ScriptedLLM
) -> None:
    session_id = await _session(client, await _project(app))
    pause = Pause()
    llm.turns = [[pause, Done()]]
    service: SessionService = app.state.sessions
    run = await service.start_chat(session_id, "Start")
    async with asyncio.timeout(10):
        await pause.reached.wait()
        await service.close()
    assert run.task.done()
    await run.events.aclose()
    assert llm.closed == 1
    assert (await service.get(session_id)).run_status is RunStatus.IDLE

    async with app.state.sessionmaker() as db:
        row = await db.get(Session, session_id)
        assert row is not None
        row.run_status = RunStatus.RUNNING
        await db.commit()
    fresh = SessionService(app.state.sessionmaker, app.state.mcp)
    await fresh.recover()
    assert (await fresh.get(session_id)).run_status is RunStatus.IDLE


async def test_cancelled_tool_batch_replays_only_completed_calls(
    app: FastAPI, client: AsyncClient, llm: ScriptedLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = await _session(client, await _project(app))
    url = f"/api/sessions/{session_id}"
    pause = Pause()
    calls = 0

    async def call_tool(*args: object, **kwargs: object) -> ToolResult:
        nonlocal calls
        calls += 1
        if calls == 2:
            await pause.wait()
        return ToolResult(text="Read")

    monkeypatch.setattr(app.state.mcp, "call_tool", call_tool)
    llm.turns = [
        [
            ToolCallRequest("first", "read_file", {}),
            ToolCallRequest("second", "read_file", {}),
            Done(),
        ],
        [TextDelta("Resumed"), Done()],
    ]
    task = asyncio.create_task(client.post(f"{url}/chat", json={"message": "Start"}))
    async with asyncio.timeout(10):
        await pause.reached.wait()
        assert (await client.post(f"{url}/cancel")).status_code == 204
        assert (await task).status_code == 200

    body = (await client.get(url)).json()
    assert [message["role"] for message in body["messages"]] == ["user", "assistant", "tool"]
    assert len(body["messages"][1]["tool_calls"]) == 2
    assert (await client.post(f"{url}/chat", json={"message": "Continue"})).status_code == 200
    replay = llm.requests[1]
    assert [call["id"] for call in replay[2]["tool_calls"]] == ["first"]
    assert replay[3] == {"role": "tool", "content": "Read", "tool_call_id": "first"}
    assert replay[4] == {"role": "user", "content": "Continue"}


async def test_simultaneous_requests_are_serialized_per_session(
    app: FastAPI, client: AsyncClient, llm: ScriptedLLM
) -> None:
    session_id = await _session(client, await _project(app))
    url = f"/api/sessions/{session_id}"
    pause = Pause()
    llm.turns = [[pause, Done()]]
    tasks = [
        asyncio.create_task(client.post(f"{url}/chat", json={"message": message}))
        for message in ("First", "Second")
    ]
    try:
        async with asyncio.timeout(10):
            await pause.reached.wait()
            finished, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            assert next(iter(finished)).result().status_code == 409
            pause.release.set()
            responses = await asyncio.gather(*tasks)
        assert sorted(response.status_code for response in responses) == [200, 409]
        assert len(llm.requests) == 1
    finally:
        await client.post(f"{url}/cancel")
        await asyncio.gather(*tasks)


async def test_different_sessions_can_run_concurrently(
    app: FastAPI, client: AsyncClient, llm: ScriptedLLM
) -> None:
    project_id = await _project(app)
    session_ids = [await _session(client, project_id), await _session(client, project_id)]
    first, second = Pause(), Pause()
    llm.turns = [[first, TextDelta("One"), Done()], [second, TextDelta("Two"), Done()]]
    tasks = [
        asyncio.create_task(
            client.post(f"/api/sessions/{session_id}/chat", json={"message": "Start"})
        )
        for session_id in session_ids
    ]
    try:
        async with asyncio.timeout(10):
            await asyncio.gather(first.reached.wait(), second.reached.wait())
            for session_id in session_ids:
                body = (await client.get(f"/api/sessions/{session_id}")).json()
                assert body["run_status"] == "running"
            first.release.set()
            second.release.set()
            assert all(response.status_code == 200 for response in await asyncio.gather(*tasks))
    finally:
        for session_id in session_ids:
            await client.post(f"/api/sessions/{session_id}/cancel")
        await asyncio.gather(*tasks)


async def test_cancel_with_full_buffer_does_not_deadlock_or_close_receiver(
    app: FastAPI, client: AsyncClient, llm: ScriptedLLM
) -> None:
    session_id = await _session(client, await _project(app))
    full = Pause()
    full.release.set()
    llm.turns = [
        [
            *[TextDelta("Token") for _ in range(Settings.CHAT_EVENT_BUFFER_SIZE)],
            full,
            TextDelta("Blocked"),
            Done(),
        ]
    ]
    service: SessionService = app.state.sessions
    run = await service.start_chat(session_id, "Start")
    try:
        async with asyncio.timeout(10):
            await full.reached.wait()
            assert run.events.statistics().current_buffer_used == Settings.CHAT_EVENT_BUFFER_SIZE
            assert (await client.post(f"/api/sessions/{session_id}/cancel")).status_code == 204
            buffered = [event async for event in run.events]
        assert len(buffered) == Settings.CHAT_EVENT_BUFFER_SIZE
        assert all(event.type == "token" for event in buffered)
        assert llm.closed == 1
        body = (await client.get(f"/api/sessions/{session_id}")).json()
        assert body["run_status"] == "idle"
        assert [message["role"] for message in body["messages"]] == ["user"]
    finally:
        await service.stop(run)
        await run.events.aclose()


async def test_gateway_setup_failure_is_reported_in_stream(
    app: FastAPI, client: AsyncClient, llm: ScriptedLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = await _session(client, await _project(app))
    monkeypatch.setattr(
        SettingsService, "gateway", AsyncMock(side_effect=LLMError("Could not configure provider"))
    )

    streamed = await _events(
        await client.post(f"/api/sessions/{session_id}/chat", json={"message": "Start"})
    )

    assert [event["event"] for event in streamed] == ["error", "done"]
    assert streamed[0]["data"]["code"] == "llm_error"
    assert streamed[1]["data"] == {"type": "done", "reason": "error", "steps": 0}
    assert (await client.get(f"/api/sessions/{session_id}")).json()["run_status"] == "idle"


async def test_cancellation_during_finalization_still_restores_idle(
    app: FastAPI, client: AsyncClient, llm: ScriptedLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = await _session(client, await _project(app))
    url = f"/api/sessions/{session_id}"
    llm.turns = [[TextDelta("Finished"), Done()]]
    service: SessionService = app.state.sessions
    pause = Pause()
    stopping = asyncio.Event()
    original_finish, original_stop = service._finish, service.stop

    async def finish(session_id: int) -> None:
        await pause.wait()
        await original_finish(session_id)

    async def stop(run: ChatRun) -> None:
        stopping.set()
        await original_stop(run)

    monkeypatch.setattr(service, "_finish", finish)
    monkeypatch.setattr(service, "stop", stop)
    chat = asyncio.create_task(client.post(f"{url}/chat", json={"message": "Start"}))
    try:
        async with asyncio.timeout(10):
            await pause.reached.wait()
            cancel = asyncio.create_task(client.post(f"{url}/cancel"))
            await stopping.wait()
            pause.release.set()
            assert (await cancel).status_code == 204
            streamed = await _events(await chat)
        assert streamed[-1]["event"] == "done"
        assert (await client.get(url)).json()["run_status"] == "idle"
    finally:
        pause.release.set()
        await client.post(f"{url}/cancel")
        await chat


async def test_cancellation_finishes_committing_a_completed_message(
    app: FastAPI, client: AsyncClient, llm: ScriptedLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = await _session(client, await _project(app))
    url = f"/api/sessions/{session_id}"
    llm.turns = [[TextDelta("Complete"), Done()]]
    service: SessionService = app.state.sessions
    pause = Pause()
    stopping = asyncio.Event()
    original_save, original_stop = service._save_message, service.stop

    async def save(session_id: int, completed: Mapping[str, Any]) -> None:
        await pause.wait()
        await original_save(session_id, completed)

    async def stop(run: ChatRun) -> None:
        stopping.set()
        await original_stop(run)

    monkeypatch.setattr(service, "_save_message", save)
    monkeypatch.setattr(service, "stop", stop)
    chat = asyncio.create_task(client.post(f"{url}/chat", json={"message": "Start"}))
    try:
        async with asyncio.timeout(10):
            await pause.reached.wait()
            cancel = asyncio.create_task(client.post(f"{url}/cancel"))
            await stopping.wait()
            pause.release.set()
            assert (await cancel).status_code == 204
            assert (await chat).status_code == 200
        body = (await client.get(url)).json()
        assert body["run_status"] == "idle"
        assert [message["content"] for message in body["messages"]] == ["Start", "Complete"]
    finally:
        pause.release.set()
        await client.post(f"{url}/cancel")
        await chat
