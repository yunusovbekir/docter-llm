"""Compare current documentation with signatures, indexed symbols, and local targets."""

import logging
import posixpath
import re
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from mcp.server.mcpserver.exceptions import ToolError

from docter_contract import (
    CheckDocDriftInput,
    CheckDocDriftOutput,
    DriftIssue,
    DriftType,
    ErrorCode,
    GetSymbolOutput,
    RunDocExamplesInput,
    SymbolKind,
    ToolErrorInfo,
)
from docter_mcp.analysis import examples
from docter_mcp.analysis.markup import anchors, links, prose_lines
from docter_mcp.config import Config
from docter_mcp.indexing.store import IndexStore
from docter_mcp.parsing import ParsingSettings, PythonParser, detect_style, parse_docstring
from docter_mcp.sandbox import Sandbox


def _parameters(symbol: GetSymbolOutput, symbols: dict[str, GetSymbolOutput]) -> list[DriftIssue]:
    if not symbol.docstring or symbol.kind is SymbolKind.MODULE:
        return []
    signature = symbol
    if symbol.kind is SymbolKind.CLASS:
        constructor = symbols.get(f"{symbol.qualname}.__init__") or symbols.get(
            f"{symbol.qualname}.__new__"
        )
        if constructor is None:
            return []
        signature = constructor
    expected = {
        param.name
        for param in signature.parameters
        if param.name not in ParsingSettings.INSTANCE_NAMES
    }
    doc = parse_docstring(symbol.docstring, detect_style([symbol.docstring]))
    documented = {
        param.name.lstrip("*")
        for param in doc.params
        if param.name.lstrip("*") not in ParsingSettings.INSTANCE_NAMES
    }
    missing, stale = sorted(expected - documented), sorted(documented - expected)
    if not missing and not stale:
        return []
    details = [
        (
            DriftType.PARAM_MISMATCH,
            f"Documented parameters {sorted(documented)} differ from signature {sorted(expected)}.",
        ),
        *[
            (DriftType.MISSING_PARAM_DOC, f"Parameter {name!r} is not documented.")
            for name in missing
        ],
        *[
            (DriftType.STALE_PARAM_DOC, f"Documented parameter {name!r} is not in the signature.")
            for name in stale
        ],
    ]
    return [
        DriftIssue(
            type=kind,
            file=symbol.file,
            line=symbol.start_line,
            symbol=symbol.qualname,
            detail=detail,
        )
        for kind, detail in details
    ]


def _references(
    path: str,
    text: str,
    names: set[str],
    roots: set[str],
    symbol: GetSymbolOutput | None = None,
) -> list[DriftIssue]:
    lines = text.splitlines(keepends=True) if symbol else prose_lines(path, text)
    issues = []
    source_offset = 0
    for number, line in enumerate(lines, 1):
        for match in re.finditer(r"(?<!`)(`{1,2})([^`\n]+)\1(?!`)", line):
            name = match[2].removeprefix("~").removesuffix("()")
            if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+", name):
                continue
            if name.split(".")[0] not in roots or name in names:
                continue
            location = number
            if symbol is not None:
                offset = symbol.source.find(match[0], source_offset)
                location = symbol.start_line + symbol.source[: max(offset, 0)].count("\n")
                if offset >= 0:
                    source_offset = offset + len(match[0])
            issues.append(
                DriftIssue(
                    type=DriftType.STALE_REFERENCE,
                    file=path,
                    line=location,
                    symbol=symbol.qualname if symbol else None,
                    detail=f"Referenced symbol {name!r} is absent from the index.",
                )
            )
    return issues


def _link_problem(
    sandbox: Sandbox, path: str, target: str, cache: dict[str, set[str]]
) -> str | None:
    try:
        url = urlsplit(target)
    except ValueError as exc:
        return f"Invalid link {target!r}: {exc}"
    if url.scheme or url.netloc:
        return None
    relative = unquote(url.path)
    if relative.startswith(("/", "\\")):
        return f"Link {target!r} is not repository-relative."
    destination = (
        posixpath.normpath(posixpath.join(posixpath.dirname(path), relative)) if relative else path
    )
    try:
        sandbox.resolve(destination)
        if sandbox.is_dir(destination):
            for name in ("index.md", "README.md", "index.rst", "README.rst"):
                candidate = posixpath.join(destination, name)
                if sandbox.is_file(candidate):
                    destination = candidate
                    break
            else:
                if not url.fragment:
                    return None
        if not sandbox.is_file(destination):
            return f"Link {target!r} points to a missing file."
        if url.fragment:
            if destination not in cache:
                cache[destination] = (
                    anchors(destination, sandbox.read_text(destination))
                    if PurePosixPath(destination).suffix.lower() in {".md", ".rst"}
                    else set()
                )
            if unquote(url.fragment) not in cache[destination]:
                return f"Link {target!r} points to a missing anchor."
    except ToolError as exc:
        error = ToolErrorInfo.parse(str(exc))
        if error is None or error.code is not ErrorCode.PATH_OUTSIDE_REPO:
            raise
        return f"Link {target!r} violates the repository boundary: {error.message}"
    return None


def check_doc_drift(request: CheckDocDriftInput, config: Config) -> CheckDocDriftOutput:
    """Read fresh docs and signatures; stale references use the last completed index."""
    sandbox = Sandbox(request.repo, config.allowed_roots)
    if request.path_prefix is not None:
        sandbox.resolve(request.path_prefix)
    with IndexStore(sandbox, config) as store:
        names = store.metadata.symbol_names()
    roots = {name.split(".")[0] for name in names}
    paths = [
        path
        for path in sorted(sandbox.files(respect_gitignore=True))
        if PurePosixPath(path).suffix.lower() in {".py", ".md", ".rst"}
        and (request.path_prefix is None or path.startswith(request.path_prefix))
    ]
    issues = []
    anchor_cache: dict[str, set[str]] = {}
    for path in paths:
        text = sandbox.read_text(path)
        if PurePosixPath(path).suffix.lower() == ".py":
            parsed = PythonParser().parse(path, text)
            for warning in parsed.warnings:
                logging.getLogger(__name__).warning(
                    "%s:%s: %s", warning.path, warning.line, warning.message
                )
            symbols = {symbol.qualname: symbol for symbol in parsed.symbols}
            for symbol in symbols.values():
                issues.extend(_parameters(symbol, symbols))
                if symbol.docstring:
                    issues.extend(_references(path, symbol.docstring, names, roots, symbol))
        else:
            issues.extend(_references(path, text, names, roots))
            for line, target in links(path, text):
                problem = _link_problem(sandbox, path, target, anchor_cache)
                if problem:
                    issues.append(
                        DriftIssue(type=DriftType.BROKEN_LINK, file=path, line=line, detail=problem)
                    )
        if config.allow_exec and not path.lower().endswith(".rst"):
            result = examples.run_doc_examples(
                RunDocExamplesInput(repo=request.repo, file_path=path), config
            )
            issues.extend(
                DriftIssue(
                    type=DriftType.FAILING_EXAMPLE,
                    file=item.file,
                    line=item.line,
                    detail=item.output or "Example failed.",
                )
                for item in result.results
                if not item.passed
            )
    issues.sort(key=lambda issue: (issue.file, issue.line, issue.type.value, issue.detail))
    return CheckDocDriftOutput(issues=issues)
