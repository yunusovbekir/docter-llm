"""Client for the Docter MCP server, which runs as a subprocess over stdio.

``MCPManager`` owns a single ``ClientSession``. The session is opened lazily on
first use, inside a dedicated background task that keeps the stdio transport and
session alive with an ``AsyncExitStack``. A single task owns the transport because
anyio task groups must be entered and exited from the same task, while requests
and app shutdown run in different tasks.
"""

import asyncio
import contextlib
import logging
import sys
from collections.abc import Mapping, Sequence
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, TextIO

import anyio
from mcp import ClientSession, StdioServerParameters, stdio_client
from mcp import types as mcp_types
from mcp.client.session import SamplingFnT
from mcp.shared.dispatcher import ProgressFnT
from mcp.shared.exceptions import MCPError as SDKMCPError
from mcp.types.jsonrpc import CONNECTION_CLOSED, REQUEST_TIMEOUT
from pydantic import BaseModel, ConfigDict

from backend.errors import DocterError

logger = logging.getLogger(__name__)

DEFAULT_STARTUP_TIMEOUT_S = 30.0
DEFAULT_REQUEST_TIMEOUT_S = 60.0
SHUTDOWN_TIMEOUT_S = 10.0

ProgressCallback = ProgressFnT
SamplingCallback = SamplingFnT

_CONNECTION_LOST_ERRORS = (anyio.ClosedResourceError, anyio.BrokenResourceError, anyio.EndOfStream)


class MCPError(DocterError):
    """The MCP server could not be started or a request to it failed."""

    status_code = 502
    code = "mcp_error"


class MCPTimeoutError(MCPError):
    """The MCP server did not respond in time."""

    status_code = 504
    code = "mcp_timeout"


class ToolResult(BaseModel):
    """Normalized result of an MCP tool call.

    Attributes:
        text: The text content blocks of the result, joined with newlines.
        structured: The tool's structured output, if it returned any.
        is_error: Whether the tool reported a failure.
    """

    model_config = ConfigDict(frozen=True)

    text: str
    structured: Any = None
    is_error: bool = False


class MCPManager:
    """Starts the Docter MCP server on demand and forwards tool calls to it."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        errlog_path: Path | None = None,
        sampling_callback: SamplingCallback | None = None,
        startup_timeout: float = DEFAULT_STARTUP_TIMEOUT_S,
    ) -> None:
        """Configure the manager without starting the server.

        Args:
            command: The server program followed by its arguments.
            env: Extra environment variables for the server process.
            errlog_path: File that receives the server's stderr; defaults to our stderr.
            sampling_callback: Handles ``sampling/createMessage`` requests from the server.
            startup_timeout: Seconds to wait for the server to start and initialize.
        """
        if not command:
            raise ValueError("command must not be empty")
        self._params = StdioServerParameters(
            command=command[0], args=list(command[1:]), env=dict(env) if env else None
        )
        self._errlog_path = errlog_path
        self._sampling_callback = sampling_callback
        self._startup_timeout = startup_timeout
        self._lock = asyncio.Lock()
        self._session: ClientSession | None = None
        self._runner: asyncio.Task[None] | None = None
        self._stop: asyncio.Event | None = None
        self._tools: list[mcp_types.Tool] | None = None

    @property
    def is_running(self) -> bool:
        """Whether a server session is currently open."""
        return self._session is not None

    async def list_tools(self) -> list[mcp_types.Tool]:
        """Return the server's tools (all pages), fetched once per session and then cached."""
        if self._tools is not None:
            return list(self._tools)
        session = await self._get_session()
        tools: list[mcp_types.Tool] = []
        cursor: str | None = None
        try:
            while True:
                params = mcp_types.PaginatedRequestParams(cursor=cursor) if cursor else None
                result = await session.list_tools(params=params)
                tools.extend(result.tools)
                cursor = result.next_cursor
                if not cursor:
                    break
        except Exception as exc:
            raise await self._wrap_failure(session, exc, "tools/list") from exc
        if self._session is session:
            self._tools = list(tools)
        return tools

    async def call_tool(
        self,
        name: str,
        args: Mapping[str, Any] | None,
        timeout: float,  # noqa: ASYNC109 - forwarded to the MCP request as its read timeout
        progress_callback: ProgressCallback | None = None,
    ) -> ToolResult:
        """Call a tool and return its normalized result.

        A tool that runs but fails is not an exception: the result has ``is_error=True``.

        Args:
            name: Tool name.
            args: Tool arguments.
            timeout: Seconds to wait for the result.
            progress_callback: Receives ``(progress, total, message)`` notifications.

        Raises:
            MCPTimeoutError: The tool did not finish within ``timeout``.
            MCPError: The server could not be started or the request failed.
        """
        session = await self._get_session()
        try:
            result = await session.call_tool(
                name,
                dict(args or {}),
                read_timeout_seconds=timeout,
                progress_callback=progress_callback,
            )
        except Exception as exc:
            raise await self._wrap_failure(session, exc, f"tool {name!r}") from exc
        return _normalize(result)

    async def restart(self) -> None:
        """Stop the server if it is running, then start a new one.

        Raises:
            MCPError: The new server could not be started.
        """
        async with self._lock:
            await self._close_locked()
            await self._start_locked()

    async def close(self) -> None:
        """Stop the server if it is running. A later call starts it again."""
        async with self._lock:
            await self._close_locked()

    async def _get_session(self) -> ClientSession:
        async with self._lock:
            if self._session is not None:
                return self._session
            return await self._start_locked()

    async def _start_locked(self) -> ClientSession:
        ready: asyncio.Future[ClientSession] = asyncio.get_running_loop().create_future()
        stop = asyncio.Event()
        runner = asyncio.create_task(self._run(ready, stop), name="docter-mcp-session")
        try:
            async with asyncio.timeout(self._startup_timeout):
                session = await asyncio.shield(ready)
        except TimeoutError:
            await _cancel(runner)
            raise MCPTimeoutError(
                f"MCP server did not start within {self._startup_timeout:g}s"
            ) from None
        except Exception as exc:
            await runner
            raise MCPError(f"Failed to start the MCP server: {_describe(exc)}") from exc
        except BaseException:
            await _cancel(runner)
            raise
        self._session, self._runner, self._stop = session, runner, stop
        logger.info("MCP server started (%s)", self._params.command)
        return session

    async def _run(self, ready: asyncio.Future[ClientSession], stop: asyncio.Event) -> None:
        """Open the transport and session, then hold them open until ``stop`` is set."""
        try:
            async with AsyncExitStack() as stack:
                errlog: TextIO = sys.stderr
                if self._errlog_path is not None:
                    errlog = stack.enter_context(
                        await asyncio.to_thread(self._errlog_path.open, "a", encoding="utf-8")
                    )
                read, write = await stack.enter_async_context(
                    stdio_client(self._params, errlog=errlog)
                )
                session = await stack.enter_async_context(
                    ClientSession(
                        read,
                        write,
                        read_timeout_seconds=DEFAULT_REQUEST_TIMEOUT_S,
                        sampling_callback=self._sampling_callback,
                    )
                )
                await session.initialize()
                ready.set_result(session)
                await stop.wait()
        except Exception as exc:
            if not ready.done():
                ready.set_exception(exc)
            else:
                logger.warning("MCP server session ended with an error: %s", _describe(exc))

    async def _close_locked(self) -> None:
        runner, stop = self._runner, self._stop
        self._session = self._runner = self._stop = None
        self._tools = None
        if runner is None or stop is None:
            return
        stop.set()
        try:
            async with asyncio.timeout(SHUTDOWN_TIMEOUT_S):
                await asyncio.shield(runner)
        except TimeoutError:
            logger.warning("MCP server did not stop within %ss; cancelling", SHUTDOWN_TIMEOUT_S)
            await _cancel(runner)
        logger.info("MCP server stopped")

    async def _discard(self, session: ClientSession) -> None:
        """Close ``session`` if it is still current, so the next call starts a new server."""
        async with self._lock:
            if self._session is session:
                await self._close_locked()

    async def _wrap_failure(self, session: ClientSession, exc: Exception, action: str) -> MCPError:
        if isinstance(exc, SDKMCPError) and exc.code == REQUEST_TIMEOUT:
            return MCPTimeoutError(f"MCP {action} timed out")
        if _is_connection_lost(exc):
            logger.warning("MCP server connection lost during %s", action)
            await self._discard(session)
            return MCPError(f"MCP server connection lost during {action}")
        return MCPError(f"MCP {action} failed: {_describe(exc)}")


def _normalize(result: mcp_types.CallToolResult) -> ToolResult:
    text = "\n".join(
        block.text for block in result.content if isinstance(block, mcp_types.TextContent)
    )
    return ToolResult(text=text, structured=result.structured_content, is_error=result.is_error)


def _is_connection_lost(exc: Exception) -> bool:
    if isinstance(exc, SDKMCPError):
        return exc.code == CONNECTION_CLOSED
    return isinstance(exc, _CONNECTION_LOST_ERRORS)


def _describe(exc: BaseException) -> str:
    """Return a one-line description of ``exc``, unwrapping exception groups."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    if isinstance(exc, SDKMCPError):
        return exc.message
    return str(exc) or type(exc).__name__


async def _cancel(task: asyncio.Task[None]) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
