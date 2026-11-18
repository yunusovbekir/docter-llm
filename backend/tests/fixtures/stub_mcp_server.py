"""Stub Docter MCP server used by the MCP client tests.

Run as a script (``python stub_mcp_server.py``); it serves over stdio. If the
``STUB_MCP_PID_FILE`` environment variable is set, the process writes its PID
there on startup so tests can kill it to simulate a crash.
"""

import argparse
import asyncio
import os
from pathlib import Path
from typing import Any

from mcp import types
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel

PID_FILE_ENV = "STUB_MCP_PID_FILE"
INDEX_CONTROL_DIR: Path | None = None

server = MCPServer("docter-stub")


class ProposedPatch(BaseModel):
    """Structured output of ``propose_patch``."""

    file_path: str
    patch: str
    rationale: str


@server.tool()
def echo(text: str) -> str:
    """Return ``text`` unchanged."""
    return text


@server.tool()
async def index_repo(path: str, ctx: Context[Any, Any], steps: int = 3, delay: float = 0.0) -> str:
    """Pretend to index ``path``, reporting progress after each of ``steps`` steps."""
    for step in range(1, steps + 1):
        await asyncio.sleep(delay)
        await ctx.report_progress(step, steps, f"Indexed {step}/{steps}")
        if INDEX_CONTROL_DIR is not None and step == 1:
            (INDEX_CONTROL_DIR / "path").write_text(path)
            while not (INDEX_CONTROL_DIR / "release").exists():  # noqa: ASYNC110 - cross-process gate
                await asyncio.sleep(0.01)
    if INDEX_CONTROL_DIR is not None and (INDEX_CONTROL_DIR / "fail").exists():
        raise ToolError("Stub indexing failed")
    return f"Indexed {path}"


@server.tool()
def propose_patch(file_path: str, patch: str, rationale: str) -> ProposedPatch:
    """Return the proposed patch as structured output."""
    return ProposedPatch(file_path=file_path, patch=patch, rationale=rationale)


def main() -> None:
    """Serve the stub tools over stdio."""
    global INDEX_CONTROL_DIR
    parser = argparse.ArgumentParser()
    parser.add_argument("--index-control-dir", type=Path)
    parser.add_argument("--sampling", action="store_true")
    args = parser.parse_args()
    INDEX_CONTROL_DIR = args.index_control_dir
    if args.sampling:

        @server.tool()
        async def sample(ctx: Context[Any, Any]) -> str:
            """Ask the client to complete a short conversation."""
            result = await ctx.session.create_message(
                [
                    types.SamplingMessage(
                        role="user", content=types.TextContent(type="text", text="Say hello")
                    ),
                    types.SamplingMessage(
                        role="assistant", content=types.TextContent(type="text", text="Hi")
                    ),
                    types.SamplingMessage(
                        role="user", content=types.TextContent(type="text", text="Again")
                    ),
                ],
                system_prompt="Be concise",
                max_tokens=16,
                temperature=0.6,
                stop_sequences=["END"],
            )
            assert isinstance(result, types.CreateMessageResult)
            return result.model_dump_json(by_alias=True, exclude_none=True)

    if INDEX_CONTROL_DIR is not None:
        (INDEX_CONTROL_DIR / "pid").write_text(str(os.getpid()))
    pid_file = os.environ.get(PID_FILE_ENV)
    if pid_file:
        Path(pid_file).write_text(str(os.getpid()))
    server.run()


if __name__ == "__main__":
    main()
