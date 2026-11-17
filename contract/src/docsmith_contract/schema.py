"""JSON Schema export of the whole contract."""

import json
from typing import Any

from docsmith_contract.errors import ErrorCode, ToolErrorInfo
from docsmith_contract.registry import TOOL_SPECS
from docsmith_contract.resources import RESOURCE_URIS
from docsmith_contract.version import CONTRACT_VERSION


def build_schema() -> dict[str, Any]:
    """Return the contract as a JSON-serializable document.

    Each tool has standalone JSON Schemas for its input and output (and its progress
    notifications, if any), so a schema can be used directly as an MCP ``inputSchema``
    or ``outputSchema``.
    """
    tools: dict[str, Any] = {}
    for name, spec in TOOL_SPECS.items():
        entry: dict[str, Any] = {
            "input": spec.input.model_json_schema(),
            "output": spec.output.model_json_schema(),
        }
        if spec.progress is not None:
            entry["progress"] = spec.progress.model_json_schema()
        tools[name.value] = entry
    return {
        "contract_version": CONTRACT_VERSION,
        "error_codes": [code.value for code in ErrorCode],
        "error": ToolErrorInfo.model_json_schema(),
        "resources": list(RESOURCE_URIS),
        "tools": tools,
    }


def render_schema() -> str:
    """Return ``build_schema()`` as the exact text stored in ``contract/schema.json``."""
    return json.dumps(build_schema(), indent=2, ensure_ascii=False) + "\n"
