"""Conversion from MCP tools to OpenAI-compatible function definitions."""

from copy import deepcopy
from typing import Any

from mcp.types import Tool


def tool_to_openai(tool: Tool) -> dict[str, Any]:
    """Preserve the tool's name, description, and complete JSON input schema."""
    function: dict[str, Any] = {
        "name": tool.name,
        "parameters": deepcopy(tool.input_schema),
    }
    if tool.description is not None:
        function["description"] = tool.description
    return {"type": "function", "function": function}
