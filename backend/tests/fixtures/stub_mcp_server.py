"""Stub Docter MCP server used by the MCP client tests.

Run as a script (``python stub_mcp_server.py``); it serves over stdio. If the
``STUB_MCP_PID_FILE`` environment variable is set, the process writes its PID
there on startup so tests can kill it to simulate a crash.
"""

import asyncio
import os
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from pydantic import BaseModel

PID_FILE_ENV = "STUB_MCP_PID_FILE"

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
    return f"Indexed {path}"


@server.tool()
def propose_patch(file_path: str, patch: str, rationale: str) -> ProposedPatch:
    """Return the proposed patch as structured output."""
    return ProposedPatch(file_path=file_path, patch=patch, rationale=rationale)


def main() -> None:
    """Serve the stub tools over stdio."""
    pid_file = os.environ.get(PID_FILE_ENV)
    if pid_file:
        Path(pid_file).write_text(str(os.getpid()))
    server.run()


if __name__ == "__main__":
    main()
