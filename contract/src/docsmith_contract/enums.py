"""Enumerations used in tool names, inputs, and outputs."""

from enum import StrEnum


class ToolName(StrEnum):
    """Names of the tools exposed by the DocSmith MCP server."""

    GET_SERVER_INFO = "get_server_info"
    INDEX_REPO = "index_repo"
    SEARCH_CODE = "search_code"
    GET_SYMBOL = "get_symbol"
    READ_FILE = "read_file"
    LIST_DOC_FILES = "list_doc_files"
    FIND_DOC_GAPS = "find_doc_gaps"
    CHECK_DOC_DRIFT = "check_doc_drift"
    RUN_DOC_EXAMPLES = "run_doc_examples"
    PROPOSE_PATCH = "propose_patch"
    TRANSLATE_FILE = "translate_file"


class SymbolKind(StrEnum):
    """Kind of a Python code symbol."""

    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"


class ParameterKind(StrEnum):
    """How a function parameter is bound, mirroring ``inspect.Parameter.kind``."""

    POSITIONAL_ONLY = "positional_only"
    POSITIONAL_OR_KEYWORD = "positional_or_keyword"
    VAR_POSITIONAL = "var_positional"
    KEYWORD_ONLY = "keyword_only"
    VAR_KEYWORD = "var_keyword"


class DocFramework(StrEnum):
    """Documentation site generator detected in a repository."""

    MKDOCS = "mkdocs"
    SPHINX = "sphinx"
    NONE = "none"


class DocFormat(StrEnum):
    """Markup format of a documentation file."""

    MARKDOWN = "markdown"
    RST = "rst"


class DocGap(StrEnum):
    """A piece of documentation a public symbol is missing."""

    DOCSTRING = "docstring"
    PARAMS = "params"
    RETURNS = "returns"
    RAISES = "raises"
    REFERENCE_PAGE = "reference_page"


class DriftType(StrEnum):
    """Kind of mismatch between documentation and code."""

    PARAM_MISMATCH = "param_mismatch"
    MISSING_PARAM_DOC = "missing_param_doc"
    STALE_PARAM_DOC = "stale_param_doc"
    STALE_REFERENCE = "stale_reference"
    BROKEN_LINK = "broken_link"
    FAILING_EXAMPLE = "failing_example"
