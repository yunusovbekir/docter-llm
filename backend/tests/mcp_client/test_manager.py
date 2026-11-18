import os
import signal
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp import types as mcp_types

from backend.mcp_client import MCPError, MCPManager, MCPTimeoutError, ToolResult
from backend.tests.fixtures.stub_mcp_server import PID_FILE_ENV

KILL_SIGNAL = getattr(signal, "SIGKILL", signal.SIGTERM)


@pytest.fixture
def pid_file(tmp_path: Path) -> Path:
    return tmp_path / "stub-mcp.pid"


@pytest.fixture
async def manager(
    stub_mcp_command: list[str], pid_file: Path, tmp_path: Path
) -> AsyncIterator[MCPManager]:
    mcp = MCPManager(
        stub_mcp_command,
        env={PID_FILE_ENV: str(pid_file)},
        errlog_path=tmp_path / "mcp-server.log",
    )
    yield mcp
    await mcp.close()


def _server_pid(pid_file: Path) -> int:
    return int(pid_file.read_text())


def _server_started(pid_file: Path) -> bool:
    return pid_file.exists()


async def test_server_starts_lazily(manager: MCPManager, pid_file: Path) -> None:
    assert not manager.is_running
    assert not _server_started(pid_file)

    await manager.call_tool("echo", {"text": "hi"}, timeout=10)

    assert manager.is_running
    assert _server_started(pid_file)


async def test_list_tools_returns_stub_tools(manager: MCPManager) -> None:
    tools = await manager.list_tools()

    assert {tool.name for tool in tools} == {"echo", "index_repo", "propose_patch"}
    patch_tool = next(tool for tool in tools if tool.name == "propose_patch")
    assert patch_tool.input_schema["required"] == ["file_path", "patch", "rationale"]


async def test_list_tools_is_cached(manager: MCPManager, monkeypatch: pytest.MonkeyPatch) -> None:
    first = await manager.list_tools()

    async def fail(*_: object, **__: object) -> None:
        raise AssertionError("list_tools should be served from the cache")

    monkeypatch.setattr(ClientSession, "list_tools", fail)

    assert [tool.name for tool in await manager.list_tools()] == [tool.name for tool in first]


async def test_list_tools_follows_pagination(
    manager: MCPManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    schema = {"type": "object", "properties": {}}
    pages = {
        None: mcp_types.ListToolsResult(
            tools=[mcp_types.Tool(name="first", input_schema=schema)], next_cursor="page-2"
        ),
        "page-2": mcp_types.ListToolsResult(
            tools=[mcp_types.Tool(name="second", input_schema=schema)]
        ),
    }
    cursors: list[str | None] = []

    async def paged_list_tools(
        _: ClientSession, *, params: mcp_types.PaginatedRequestParams | None = None
    ) -> mcp_types.ListToolsResult:
        cursor = params.cursor if params else None
        cursors.append(cursor)
        return pages[cursor]

    monkeypatch.setattr(ClientSession, "list_tools", paged_list_tools)

    assert [tool.name for tool in await manager.list_tools()] == ["first", "second"]
    assert cursors == [None, "page-2"]


async def test_call_tool_returns_normalized_result(manager: MCPManager) -> None:
    result = await manager.call_tool("echo", {"text": "hello"}, timeout=10)

    assert result == ToolResult(text="hello", structured={"result": "hello"}, is_error=False)


async def test_propose_patch_returns_its_input_as_structured_output(
    manager: MCPManager,
) -> None:
    args = {"file_path": "README.md", "patch": "@@ -1 +1 @@\n-a\n+b\n", "rationale": "Typo"}

    result = await manager.call_tool("propose_patch", args, timeout=10)

    assert result.structured == {**args, "is_new_file": False}
    assert not result.is_error


async def test_call_tool_forwards_progress(manager: MCPManager) -> None:
    updates: list[tuple[float, float | None, str | None]] = []

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        updates.append((progress, total, message))

    result = await manager.call_tool(
        "index_repo", {"path": "/repo", "steps": 3}, timeout=10, progress_callback=on_progress
    )

    assert result.text == "Indexed /repo"
    assert updates == [(1, 3, "Indexed 1/3"), (2, 3, "Indexed 2/3"), (3, 3, "Indexed 3/3")]


async def test_tool_failure_is_reported_in_the_result(manager: MCPManager) -> None:
    unknown = await manager.call_tool("does_not_exist", {}, timeout=10)
    bad_args = await manager.call_tool("echo", {}, timeout=10)

    assert unknown.is_error
    assert "Unknown tool" in unknown.text
    assert bad_args.is_error
    assert bad_args.structured is None


async def test_call_tool_timeout_raises_and_keeps_the_session(manager: MCPManager) -> None:
    with pytest.raises(MCPTimeoutError) as exc_info:
        await manager.call_tool("index_repo", {"path": "/repo", "delay": 10}, timeout=0.2)

    assert exc_info.value.code == "mcp_timeout"
    assert exc_info.value.status_code == 504
    assert (await manager.call_tool("echo", {"text": "still up"}, timeout=10)).text == "still up"


async def test_restart_after_server_crash(manager: MCPManager, pid_file: Path) -> None:
    await manager.call_tool("echo", {"text": "hi"}, timeout=10)
    crashed_pid = _server_pid(pid_file)

    os.kill(crashed_pid, KILL_SIGNAL)
    with pytest.raises(MCPError) as exc_info:
        await manager.call_tool("echo", {"text": "hi"}, timeout=10)
    assert type(exc_info.value) is MCPError
    assert "connection lost" in exc_info.value.message

    await manager.restart()

    assert manager.is_running
    assert _server_pid(pid_file) != crashed_pid
    assert (await manager.call_tool("echo", {"text": "back"}, timeout=10)).text == "back"
    assert {tool.name for tool in await manager.list_tools()} == {
        "echo",
        "index_repo",
        "propose_patch",
    }


async def test_call_after_crash_starts_a_new_server(manager: MCPManager, pid_file: Path) -> None:
    await manager.call_tool("echo", {"text": "hi"}, timeout=10)
    crashed_pid = _server_pid(pid_file)
    os.kill(crashed_pid, KILL_SIGNAL)
    with pytest.raises(MCPError):
        await manager.call_tool("echo", {"text": "hi"}, timeout=10)

    assert not manager.is_running
    assert (await manager.call_tool("echo", {"text": "again"}, timeout=10)).text == "again"
    assert _server_pid(pid_file) != crashed_pid


async def test_restart_replaces_a_running_server(manager: MCPManager, pid_file: Path) -> None:
    await manager.call_tool("echo", {"text": "hi"}, timeout=10)
    first_pid = _server_pid(pid_file)

    await manager.restart()

    assert _server_pid(pid_file) != first_pid
    assert (await manager.call_tool("echo", {"text": "hi"}, timeout=10)).text == "hi"


async def test_close_stops_the_server(manager: MCPManager) -> None:
    await manager.list_tools()

    await manager.close()

    assert not manager.is_running


@pytest.mark.parametrize(
    "command",
    [
        ["/nonexistent/docter-mcp"],
        [sys.executable, "-c", "import sys; sys.exit(3)"],
    ],
    ids=["missing-executable", "exits-immediately"],
)
async def test_start_failure_raises_mcp_error(command: list[str], tmp_path: Path) -> None:
    mcp = MCPManager(command, errlog_path=tmp_path / "mcp-server.log")

    with pytest.raises(MCPError) as exc_info:
        await mcp.list_tools()

    assert exc_info.value.code == "mcp_error"
    assert exc_info.value.message.startswith("Failed to start the MCP server")
    assert not mcp.is_running


async def test_startup_timeout_raises_mcp_timeout(tmp_path: Path) -> None:
    hanging = [sys.executable, "-c", "import time; time.sleep(30)"]
    mcp = MCPManager(hanging, errlog_path=tmp_path / "mcp-server.log", startup_timeout=0.5)

    with pytest.raises(MCPTimeoutError):
        await mcp.list_tools()

    assert not mcp.is_running


def test_empty_command_is_rejected() -> None:
    with pytest.raises(ValueError, match="command"):
        MCPManager([])
