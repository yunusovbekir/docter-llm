"""Repo-bound documentation resources. Target files are read only through the sandbox."""

import yaml
from mcp.server.mcpserver.exceptions import ResourceError, ToolError

from docter_contract import ErrorCode, glossary_uri
from docter_mcp.sandbox import Sandbox, fail

DEFAULT_STYLE_GUIDE = """# Documentation style

Use the project's existing docstring convention and terminology.
Describe public parameters, return values, and directly raised exceptions.
Keep examples concise and accurate. Do not invent behavior or reference missing symbols.
"""


def style_guide(sandbox: Sandbox) -> str:
    """Read the repository's style guide, using the built-in guide only when absent."""
    try:
        return sandbox.read_text(".docter/style.md")
    except ToolError as exc:
        # A directory, unreadable file or invalid UTF-8 is not a missing style guide.
        if not sandbox.exists(".docter/style.md"):
            return DEFAULT_STYLE_GUIDE
        raise ResourceError(str(exc)) from exc


def glossary(sandbox: Sandbox, lang: str) -> str:
    """Select a BCP 47 language's mapping from .docter/glossary.yaml."""
    try:
        glossary_uri(lang)
    except ValueError as exc:
        raise ResourceError(str(exc)) from exc
    try:
        text = sandbox.read_text(".docter/glossary.yaml")
        document: object = yaml.safe_load(text)
        if not isinstance(document, dict):
            raise ResourceError("Glossary must be a mapping from language tags to term mappings.")
        if lang not in document:
            raise fail(ErrorCode.FILE_NOT_FOUND, f"No glossary is defined for language {lang!r}.")
        terms = document[lang]
        if not isinstance(terms, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in terms.items()
        ):
            raise ResourceError(f"Glossary for {lang!r} must map strings to strings.")
        return yaml.safe_dump(terms, allow_unicode=True, sort_keys=True)
    except (ToolError, yaml.YAMLError) as exc:
        raise ResourceError(str(exc)) from exc
