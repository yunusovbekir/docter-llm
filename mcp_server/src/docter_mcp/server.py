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
    CheckDocDriftInput,
    CheckDocDriftOutput,
    FindDocGapsInput,
    FindDocGapsOutput,
    GetServerInfoInput,
    GetServerInfoOutput,
    GetSymbolInput,
    GetSymbolOutput,
    IndexRepoInput,
    IndexRepoOutput,
    ListDocFilesInput,
    ListDocFilesOutput,
    ProposePatchInput,
    ProposePatchOutput,
    ReadFileInput,
    ReadFileOutput,
    RunDocExamplesInput,
    RunDocExamplesOutput,
    SearchCodeInput,
    SearchCodeOutput,
    ToolName,
    TranslateFileInput,
    TranslateFileOutput,
)
from docter_mcp import patching, resources, tools, translation
from docter_mcp.analysis import drift, examples, gaps
from docter_mcp.config import Config
from docter_mcp.indexing import service as indexing
from docter_mcp.prompts import register_prompts
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

    def find_doc_gaps(request: FindDocGapsInput) -> FindDocGapsOutput:
        """Find missing documentation in current Python source; no index is required.

        Reports missing docstrings, signature params (excluding self/cls), descriptions of
        annotated non-None returns, and raises sections for directly raised exceptions.
        When docs/ exists, also flags public qualnames absent from all .md/.rst files.
        Items are ordered by file and line. path_prefix limits source files; include_private
        adds private findings without changing public docstring coverage. Respects .gitignore,
        logs and skips syntax-invalid Python, and never writes or executes repository code.
        """
        return gaps.find_doc_gaps(request, settings)

    def propose_patch(request: ProposePatchInput) -> ProposePatchOutput:
        """Propose a change for review as a Git-compatible unified diff; never write files.

        Supply a rationale and exactly one of edits or new_content. Edits replace exact text
        sequentially in an existing UTF-8 file; each old_str must match exactly once at that
        step. EDIT_NOT_FOUND and EDIT_AMBIGUOUS include context for retrying with exact text.
        new_content replaces a whole file or creates a new one. Text, line endings, and final
        newlines are kept exactly as supplied. Safe symlinks resolve to their target path.
        Returns file_path, patch, rationale, and is_new_file; unchanged content gives no diff.
        No index is required, and no files or directories are created, even temporarily.
        """
        return patching.propose_patch(request, settings)

    def check_doc_drift(request: CheckDocDriftInput) -> CheckDocDriftOutput:
        """Find stale documentation after index_repo; returns typed issues with file/line.

        Compares current docstring parameters with signatures, excluding self/cls and
        normalizing *args/**kwargs. Reports aggregate param_mismatch plus individual missing
        and stale params. Checks backticked repository-qualified symbols against the index
        and local Markdown/RST links and anchors on disk; external URLs are not fetched.
        path_prefix limits findings, not reference targets. Respects .gitignore.
        With DOCSMITH_ALLOW_EXEC=true also runs examples via run_doc_examples and reports
        failures. WARNING: opt-in execution runs arbitrary repo code with server permissions.
        Otherwise this tool only reads files and never executes repository code.
        """
        return drift.check_doc_drift(request, settings)

    def run_doc_examples(request: RunDocExamplesInput) -> RunDocExamplesOutput:
        """Run documentation examples only in repositories whose code you trust.

        WARNING: executes arbitrary repository code with the server's permissions; a
        subprocess is NOT a security sandbox and examples may read/write files or use
        the network. Raises EXECUTION_DISABLED unless DOCSMITH_ALLOW_EXEC=true (or the
        DOCTER_ALLOW_EXEC compatibility setting). No index is needed. Runs each Python
        docstring's doctest transcript and each Markdown `python runnable` fenced block
        in a separate subprocess inside the repo, with a 20s timeout. Returns file, line,
        passed, and captured output/failure details. Imports repo modules for doctests;
        adds repo and src/ to Python's import path and disables bytecode writes. Optional
        file_path selects one file; otherwise respects .gitignore. Unmarked code is skipped.
        """
        return examples.run_doc_examples(request, settings)

    async def translate_file(request: TranslateFileInput, ctx: Context) -> TranslateFileOutput:
        """Propose a Markdown translation using MCP client sampling; no files are written.

        Requires sampling support and the target repository's glossary for target_language.
        Protects code, URLs, link destinations, HTML tags, and frontmatter keys. Invalid
        placeholder responses are retried once, then retain source text with a warning.
        Uses MkDocs i18n folder layout when configured, otherwise name.<language>.md.
        Returns a new-file or update diff from the same builder as propose_patch.
        """
        return await translation.translate_file(request, settings, ctx)

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
            _tool(ToolName.FIND_DOC_GAPS, find_doc_gaps),
            _tool(ToolName.CHECK_DOC_DRIFT, check_doc_drift),
            _tool(ToolName.RUN_DOC_EXAMPLES, run_doc_examples),
            _tool(ToolName.PROPOSE_PATCH, propose_patch),
            _tool(ToolName.TRANSLATE_FILE, translate_file),
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

    register_prompts(server, repo)
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
