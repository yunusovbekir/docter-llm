# docter-contract

The shared tool contract between the Docter MCP server (`mcp_server/`) and its clients,
such as the backend (`backend/`). Both sides import these models; neither redefines them.

It defines:

- `CONTRACT_VERSION` and the `ToolName` enum.
- The pydantic input and output model of every tool, registered in `TOOL_SPECS`.
  Every tool input except `get_server_info` takes `repo`, the absolute path to the
  repository root. File paths are relative to that root; line numbers are 1-based.
- `IndexProgress`: the progress notifications `index_repo` sends.
- `ErrorCode` and `ToolErrorInfo`: tools raise `ToolError(ToolErrorInfo(...).to_message())`,
  which reads `"CODE: message"`; clients recover it with `ToolErrorInfo.parse(text)`.
- Resource URIs: `docter://style-guide` and `docter://glossary/{lang}`.

## JSON Schema

`schema.json` holds the JSON Schema of every model. A snapshot test fails when it is out
of date. Regenerate it after changing the contract (and bump `CONTRACT_VERSION`):

```bash
uv run python contract/scripts/export_schema.py           # write schema.json
uv run python contract/scripts/export_schema.py --check   # verify only
```
