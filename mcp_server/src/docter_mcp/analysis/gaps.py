"""Find missing documentation in current Python source, without requiring an index."""

import logging
import re
from pathlib import PurePosixPath

from docter_contract import (
    DocGap,
    DocGapItem,
    FindDocGapsInput,
    FindDocGapsOutput,
    GetSymbolOutput,
)
from docter_mcp.config import Config
from docter_mcp.parsing import ParsingSettings, PythonParser, detect_style, parse_docstring
from docter_mcp.sandbox import Sandbox


def find_doc_gaps(request: FindDocGapsInput, config: Config) -> FindDocGapsOutput:
    """Report omissions in selected symbols; coverage counts public symbols with docstrings."""
    sandbox = Sandbox(request.repo, config.allowed_roots)
    if request.path_prefix is not None:
        sandbox.resolve(request.path_prefix)
    paths = sorted(sandbox.files(respect_gitignore=True))
    references: set[str] | None = None
    if sandbox.is_dir("docs"):
        references = {
            name
            for path in paths
            if PurePosixPath(path).suffix.lower() in {".md", ".rst"}
            for name in re.findall(r"\b\w+(?:\.\w+)*\b", sandbox.read_text(path))
        }

    parser = PythonParser()
    total_public = documented = 0
    items = []
    for path in paths:
        if PurePosixPath(path).suffix.lower() != ".py" or (
            request.path_prefix is not None and not path.startswith(request.path_prefix)
        ):
            continue
        parsed = parser.parse(path, sandbox.read_text(path))
        for warning in parsed.warnings:
            logging.getLogger(__name__).warning(
                "%s:%s: %s", warning.path, warning.line, warning.message
            )
        for symbol in parsed.symbols:
            if symbol.is_public:
                total_public += 1
                documented += bool(symbol.docstring and symbol.docstring.strip())
            elif not request.include_private:
                continue
            missing = _missing(symbol, references)
            if missing:
                items.append(
                    DocGapItem(
                        qualname=symbol.qualname,
                        kind=symbol.kind,
                        file=symbol.file,
                        line=symbol.start_line,
                        missing=missing,
                    )
                )
    items.sort(key=lambda item: (item.file, item.line, item.qualname))
    return FindDocGapsOutput(
        coverage_percent=100.0 * documented / total_public if total_public else 100.0,
        total_public=total_public,
        documented=documented,
        items=items,
    )


def _missing(symbol: GetSymbolOutput, references: set[str] | None) -> list[DocGap]:
    text = (symbol.docstring or "").strip()
    doc = parse_docstring(text, detect_style([text]))
    missing = []
    if not text:
        missing.append(DocGap.DOCSTRING)
    documented_params = {param.name.lstrip("*") for param in doc.params}
    if any(
        param.name not in documented_params
        for param in symbol.parameters
        if param.name not in ParsingSettings.INSTANCE_NAMES
    ):
        missing.append(DocGap.PARAMS)
    if (
        symbol.returns is not None
        and symbol.returns.strip(" \t\r\n'\"") not in {"None", "NoneType", "type(None)"}
        and (doc.returns is None or not (doc.returns.description or "").strip())
    ):
        missing.append(DocGap.RETURNS)
    if symbol.raises and not doc.raises:
        missing.append(DocGap.RAISES)
    if symbol.is_public and references is not None and symbol.qualname not in references:
        missing.append(DocGap.REFERENCE_PAGE)
    return missing
