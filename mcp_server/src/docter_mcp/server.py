"""Docter stdio server, implemented with MCP 2's renamed FastMCP API (MCPServer)."""

import argparse
import logging
from collections.abc import Awaitable, Callable
from importlib.metadata import version
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ToolError
from mcp.server.mcpserver.tools import Tool
from mcp.server.mcpserver.utilities.func_metadata import FuncMetadata
from pydantic import BaseModel

from docter_contract import (
    GLOSSARY_URI_TEMPLATE,
    STYLE_GUIDE_URI,
    TOOL_SPECS,
    GetServerInfoInput,
    GetServerInfoOutput,
    GetSymbolInput,
    GetSymbolOutput,
    IndexRepoInput,
    IndexRepoOutput,
    ListDocFilesInput,
    ListDocFilesOutput,
    ReadFileInput,
    ReadFileOutput,
    SearchCodeInput,
    SearchCodeOutput,
    ToolName,
)
from docter_mcp import resources, tools
from docter_mcp.config import Config
from docter_mcp.indexing import service as indexing
from docter_mcp.sandbox import Sandbox


class _ContractMetadata(FuncMetadata):
    """Keep MCP's flat arguments identical to the shared contract, not a nested request."""

    input_model: type[BaseModel]

    def validate_arguments(self, arguments_to_validate: dict[str, Any]) -> dict[str, Any]:
        return {"request": self.input_model.model_validate(arguments_to_validate)}


def _tool(name: ToolName, handler: Callable[..., BaseModel | Awaitable[BaseModel]]) -> Tool:
    spec = TOOL_SPECS[name]
    tool = Tool.from_function(handler, name=name.value, structured_output=True)
    tool.parameters = spec.input.model_json_schema()
    tool.fn_metadata = _ContractMetadata(
        arg_model=tool.fn_metadata.arg_model,
        input_model=spec.input,
        output_model=spec.output,
        output_schema=spec.output.model_json_schema(),
    )
    return tool


def create_server(
    config: Config | None = None, resource_repo: str | Path | None = None
) -> MCPServer[None]:
    """Create an isolated server; resource binding never changes with tool calls."""
    settings = Config.from_env() if config is None else config
    repo = Path.cwd() if resource_repo is None else Path(resource_repo).absolute()

    def get_server_info(request: GetServerInfoInput) -> GetServerInfoOutput:
        """Use first to discover versions, exposed tools/resources, and execution support."""
        return tools.get_server_info(request, settings)

    def read_file(request: ReadFileInput) -> ReadFileOutput:
        """Read a UTF-8 repository file, optionally an inclusive 1-based line range.

        Returns content and total line count. Paths are repo-relative; Git metadata and
        escaping symlinks are rejected. This tool never writes or executes files.
        """
        return tools.read_file(request, settings)

    def list_doc_files(request: ListDocFilesInput) -> ListDocFilesOutput:
        """Find .md/.rst files with titles and detect MkDocs or Sphinx configuration.

        Searches the repository without following directory symlinks or reading .git.
        MkDocs takes precedence when both frameworks are present; configs are never executed.
        """
        return tools.list_doc_files(request, settings)

    async def index_repo(request: IndexRepoInput, ctx: Context) -> IndexRepoOutput:
        """Build or refresh a repository index before search_code or get_symbol.

        Parses Python symbols and Markdown/reStructuredText sections, respecting .gitignore.
        Returns file/symbol/chunk counts and duration; reports progress. Only changed files
        are re-embedded unless force is true. Writes only to configured index storage,
        never to the target repository; syntax-invalid Python files are logged and skipped.
        """
        return await indexing.index_repo(request, settings, ctx)

    def search_code(request: SearchCodeInput) -> SearchCodeOutput:
        """Find indexed Python symbols or documentation with a natural-language/code query.

        Returns up to k source snippets ranked by semantic similarity plus exact/partial
        symbol-name boosts, optionally filtered by symbol kind. Documentation uses kind
        module and has no qualname. Results reflect the last index, not current disk.
        Raises NOT_INDEXED until index_repo completes or after the configured model changes.
        """
        return indexing.search_code(request, settings)

    def get_symbol(request: GetSymbolInput) -> GetSymbolOutput:
        """Retrieve one exact dotted qualname from a completed repository index.

        Returns its signature, docstring, parameters, and fresh source read from disk.
        Reparses the indexed file so line ranges and metadata are current. Does not discover
        newly added symbols until reindexing. Raises NOT_INDEXED or SYMBOL_NOT_FOUND.
        """
        return indexing.get_symbol(request, settings)

    server: MCPServer[None] = MCPServer(
        "docter-mcp",
        version=version("docter-mcp"),
        tools=[
            _tool(ToolName.GET_SERVER_INFO, get_server_info),
            _tool(ToolName.INDEX_REPO, index_repo),
            _tool(ToolName.SEARCH_CODE, search_code),
            _tool(ToolName.GET_SYMBOL, get_symbol),
            _tool(ToolName.READ_FILE, read_file),
            _tool(ToolName.LIST_DOC_FILES, list_doc_files),
        ],
    )

    @server.resource(
        STYLE_GUIDE_URI,
        mime_type="text/markdown",
        description="Documentation style from the startup repository, or a built-in default.",
    )
    def style_guide() -> str:
        try:
            return resources.style_guide(Sandbox(repo, settings.allowed_roots))
        except ToolError as exc:
            raise ResourceError(str(exc)) from exc

    @server.resource(
        GLOSSARY_URI_TEMPLATE,
        mime_type="application/yaml",
        description="Terms for a BCP 47 language from the startup repository's glossary.",
    )
    def glossary(lang: str) -> str:
        try:
            return resources.glossary(Sandbox(repo, settings.allowed_roots), lang)
        except ToolError as exc:
            raise ResourceError(str(exc)) from exc

    return server


def main() -> None:
    """Run the console script with stdio reserved exclusively for MCP messages."""
    parser = argparse.ArgumentParser(description="Docter MCP server (stdio)")
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Repository for resources")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    try:
        config = Config.from_env()
    except ValueError as exc:
        parser.error(str(exc))
    create_server(config, args.repo).run(transport="stdio")


if __name__ == "__main__":
    main()
