"""Input and output models for every DocSmith tool.

Every tool input except ``get_server_info`` extends ``RepoInput`` and therefore takes
``repo``, the absolute path to the repository root. File paths in inputs and outputs are
relative to that root and use ``/`` separators. Line numbers are 1-based and inclusive.
"""

from typing import Annotated, Self

from pydantic import Field, model_validator

from docsmith_contract._base import (
    ContractModel,
    Count,
    LanguageTag,
    LineNumber,
    PathPrefix,
    RepoInput,
    RepoRelativePath,
    check_line_range,
)
from docsmith_contract.enums import (
    DocFormat,
    DocFramework,
    DocGap,
    DriftType,
    ParameterKind,
    SymbolKind,
    ToolName,
)

SEMVER_PATTERN = r"^\d+\.\d+\.\d+$"

# get_server_info


class GetServerInfoInput(ContractModel):
    """``get_server_info`` takes no arguments."""


class ServerCapabilities(ContractModel):
    """What this server instance supports."""

    tools: list[ToolName] = Field(description="Tools this server exposes.")
    resources: list[str] = Field(description="Resource URIs and URI templates this server serves.")
    execution_enabled: bool = Field(
        description="Whether run_doc_examples may execute code. If false, it fails with "
        "EXECUTION_DISABLED."
    )


class GetServerInfoOutput(ContractModel):
    """Versions and capabilities of the server."""

    contract_version: str = Field(
        pattern=SEMVER_PATTERN, description="Version of the tool contract the server implements."
    )
    server_version: str = Field(min_length=1, description="Version of the server package.")
    capabilities: ServerCapabilities


# index_repo


class IndexRepoInput(RepoInput):
    """Build or refresh the code index of a repository."""

    force: bool = Field(
        default=False, description="Rebuild the whole index even if it is already up to date."
    )


class IndexRepoOutput(ContractModel):
    """Summary of an indexing run."""

    files_indexed: Count = Field(description="Number of files indexed in this run.")
    symbols: Count = Field(description="Number of symbols in the index.")
    chunks: Count = Field(description="Number of searchable chunks in the index.")
    duration_ms: Count = Field(description="Wall-clock duration of the run in milliseconds.")


class IndexProgress(ContractModel):
    """Payload of the MCP progress notifications sent while ``index_repo`` runs."""

    progress: float = Field(ge=0, description="Work done so far. Increases with each notification.")
    total: float | None = Field(default=None, ge=0, description="Total amount of work, if known.")
    message: str | None = Field(default=None, description="Human-readable description of the step.")

    @model_validator(mode="after")
    def _progress_within_total(self) -> Self:
        if self.total is not None and self.progress > self.total:
            raise ValueError("progress must not exceed total")
        return self


# search_code


class SearchCodeInput(RepoInput):
    """Semantic search over the indexed code."""

    query: str = Field(min_length=1, description="Natural-language description or code fragment.")
    k: int = Field(default=8, ge=1, le=50, description="Maximum number of results to return.")
    kind: SymbolKind | None = Field(
        default=None, description="Only return chunks of this symbol kind."
    )


class SearchHit(ContractModel):
    """A code chunk that matches a search query."""

    file: RepoRelativePath
    qualname: str | None = Field(
        default=None, description="Qualified name of the enclosing symbol, if any."
    )
    kind: SymbolKind
    start_line: LineNumber
    end_line: LineNumber
    snippet: str = Field(description="Source text of the chunk.")
    score: float = Field(description="Relevance score. Higher is more relevant.")

    @model_validator(mode="after")
    def _valid_line_range(self) -> Self:
        check_line_range(self.start_line, self.end_line)
        return self


class SearchCodeOutput(ContractModel):
    """Search results, most relevant first."""

    results: list[SearchHit]


# get_symbol


class GetSymbolInput(RepoInput):
    """Look up one symbol by its qualified name."""

    qualname: str = Field(
        min_length=1,
        description="Dotted qualified name, for example 'mylib.client.Client.send'.",
    )


class SymbolParameter(ContractModel):
    """A parameter in a function or method signature."""

    name: str = Field(min_length=1)
    annotation: str | None = Field(
        default=None, description="Type annotation as written in the source, if any."
    )
    default: str | None = Field(
        default=None, description="Default value expression as written in the source, if any."
    )
    kind: ParameterKind


class GetSymbolOutput(ContractModel):
    """Signature, docstring, and source of a symbol."""

    qualname: str = Field(min_length=1)
    kind: SymbolKind
    file: RepoRelativePath
    start_line: LineNumber
    end_line: LineNumber
    signature: str = Field(description="Signature as written in the source.")
    parameters: list[SymbolParameter]
    returns: str | None = Field(
        default=None, description="Return annotation as written in the source, if any."
    )
    raises: list[str] = Field(description="Exception names the symbol raises directly.")
    decorators: list[str] = Field(description="Decorator expressions, outermost first.")
    docstring: str | None = Field(default=None, description="The docstring, if any.")
    is_public: bool = Field(description="Whether the symbol is part of the public API.")
    source: str = Field(description="Full source text of the symbol.")

    @model_validator(mode="after")
    def _valid_line_range(self) -> Self:
        check_line_range(self.start_line, self.end_line)
        return self


# read_file


class ReadFileInput(RepoInput):
    """Read a text file in the repository, optionally only a range of lines."""

    file_path: RepoRelativePath
    start_line: LineNumber | None = Field(
        default=None, description="First line to return. Defaults to the start of the file."
    )
    end_line: LineNumber | None = Field(
        default=None, description="Last line to return. Defaults to the end of the file."
    )

    @model_validator(mode="after")
    def _valid_line_range(self) -> Self:
        check_line_range(self.start_line, self.end_line)
        return self


class ReadFileOutput(ContractModel):
    """Content of a file, or of the requested line range."""

    file_path: RepoRelativePath
    content: str
    total_lines: Count = Field(description="Number of lines in the whole file.")


# list_doc_files


class ListDocFilesInput(RepoInput):
    """List the documentation files of a repository."""


class DocFile(ContractModel):
    """A documentation source file."""

    path: RepoRelativePath
    format: DocFormat
    title: str | None = Field(default=None, description="Page title, if one is declared.")


class ListDocFilesOutput(ContractModel):
    """Documentation framework and files found in the repository."""

    framework: DocFramework
    files: list[DocFile]


# find_doc_gaps


class FindDocGapsInput(RepoInput):
    """Report documentation coverage and the symbols that are missing docs."""

    include_private: bool = Field(
        default=False, description="Also report private symbols (names starting with '_')."
    )
    path_prefix: PathPrefix | None = None


class DocGapItem(ContractModel):
    """A symbol that is missing documentation."""

    qualname: str = Field(min_length=1)
    kind: SymbolKind
    file: RepoRelativePath
    line: LineNumber
    missing: list[DocGap] = Field(min_length=1, description="What documentation is missing.")


class FindDocGapsOutput(ContractModel):
    """Documentation coverage report."""

    coverage_percent: float = Field(
        ge=0, le=100, description="Share of public symbols that are documented, in percent."
    )
    total_public: Count = Field(description="Number of public symbols considered.")
    documented: Count = Field(description="Number of public symbols that have a docstring.")
    items: list[DocGapItem]

    @model_validator(mode="after")
    def _documented_within_total(self) -> Self:
        if self.documented > self.total_public:
            raise ValueError("documented must not exceed total_public")
        return self


# check_doc_drift


class CheckDocDriftInput(RepoInput):
    """Find documentation that no longer matches the code."""

    path_prefix: PathPrefix | None = None


class DriftIssue(ContractModel):
    """A place where documentation and code disagree."""

    type: DriftType
    file: RepoRelativePath
    line: LineNumber
    symbol: str | None = Field(
        default=None, description="Qualified name of the affected symbol, if any."
    )
    detail: str = Field(min_length=1, description="What is wrong.")


class CheckDocDriftOutput(ContractModel):
    """Drift issues found."""

    issues: list[DriftIssue]


# run_doc_examples


class RunDocExamplesInput(RepoInput):
    """Run the code examples in docstrings and documentation files."""

    file_path: RepoRelativePath | None = Field(
        default=None, description="Only run examples in this file. Defaults to all files."
    )


class ExampleResult(ContractModel):
    """Outcome of one code example."""

    file: RepoRelativePath
    line: LineNumber
    passed: bool
    output: str = Field(description="Captured output, or the failure details.")


class RunDocExamplesOutput(ContractModel):
    """Results of the examples that were run."""

    results: list[ExampleResult]


# propose_patch


class Edit(ContractModel):
    """Replace one exact occurrence of ``old_str`` with ``new_str``."""

    old_str: str = Field(
        min_length=1, description="Exact text to replace. It must occur exactly once in the file."
    )
    new_str: str = Field(description="Replacement text.")


class ProposePatchInput(RepoInput):
    """Propose a change to one file as a unified diff. The file is never modified.

    Provide exactly one of ``edits`` (to change an existing file) or ``new_content``
    (to replace the whole file or create a new one).
    """

    file_path: RepoRelativePath
    rationale: str = Field(min_length=1, description="Why the change is needed.")
    edits: Annotated[list[Edit], Field(min_length=1)] | None = Field(
        default=None, description="Edits applied in order. Mutually exclusive with new_content."
    )
    new_content: str | None = Field(
        default=None, description="Full new file content. Mutually exclusive with edits."
    )

    @model_validator(mode="after")
    def _exactly_one_change(self) -> Self:
        if (self.edits is None) == (self.new_content is None):
            raise ValueError("provide exactly one of edits or new_content")
        return self


class ProposePatchOutput(ContractModel):
    """A proposed change, as a unified diff against the current file."""

    file_path: RepoRelativePath
    patch: str = Field(description="Unified diff of the change.")
    rationale: str
    is_new_file: bool = Field(description="Whether the patch creates the file.")


# translate_file


class TranslateFileInput(RepoInput):
    """Translate a documentation file into another language."""

    file_path: RepoRelativePath
    target_language: LanguageTag


class TranslateFileOutput(ContractModel):
    """A proposed translation, as a unified diff that creates or updates the target file."""

    source_path: RepoRelativePath
    target_path: RepoRelativePath
    patch: str = Field(description="Unified diff that writes the translated file.")
    rationale: str
    is_new_file: bool = Field(description="Whether the patch creates the target file.")
    warnings: list[str] = Field(description="Problems a reviewer should check.")
