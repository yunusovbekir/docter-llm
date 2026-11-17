"""Docstring style detection and section parsing, built on ``docstring_parser``."""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from docstring_parser import Docstring, ParseError, parse

from docter_mcp.parsing.config import DocstringStyle as DocstringStyle
from docter_mcp.parsing.config import ParsingSettings


@dataclass(frozen=True, slots=True)
class DocParam:
    """A documented parameter. ``name`` is as written, for example ``*args``."""

    name: str
    type_name: str | None
    description: str | None


@dataclass(frozen=True, slots=True)
class DocReturns:
    """The documented return value."""

    type_name: str | None
    description: str | None


@dataclass(frozen=True, slots=True)
class DocRaises:
    """A documented exception."""

    type_name: str | None
    description: str | None


@dataclass(frozen=True, slots=True)
class ParsedDocstring:
    """The structured sections of a docstring."""

    style: DocstringStyle
    params: tuple[DocParam, ...] = ()
    returns: DocReturns | None = None
    raises: tuple[DocRaises, ...] = ()


def detect_style(docstrings: Iterable[str | None]) -> DocstringStyle:
    """Return the dominant docstring style of a project.

    Each docstring votes for the style that recognizes the most sections in it; docstrings
    without sections do not vote. Ties go to google, then numpy, then rest. Returns
    ``ParsingSettings.DEFAULT_STYLE`` if nothing votes.
    """
    votes: Counter[DocstringStyle] = Counter()
    for text in docstrings:
        if text:
            style = _best_style(text)
            if style is not None:
                votes[style] += 1
    if not votes:
        return ParsingSettings.DEFAULT_STYLE
    return max(ParsingSettings.LIBRARY_STYLES, key=lambda style: votes[style])


def parse_docstring(text: str, style: DocstringStyle) -> ParsedDocstring:
    """Parse the params, returns, and raises sections of a docstring in the given style.

    A docstring that does not follow the style yields empty sections.
    """
    doc = _parse(text, style)
    if doc is None:
        return ParsedDocstring(style=style)
    params = tuple(
        DocParam(name=meta.arg_name, type_name=meta.type_name, description=meta.description)
        for meta in doc.params
        if meta.args and meta.args[0] in ParsingSettings.PARAM_KEYS
    )
    returns = next(
        (
            DocReturns(type_name=meta.type_name, description=meta.description)
            for meta in doc.many_returns
            if not meta.is_generator
        ),
        None,
    )
    raises = tuple(
        DocRaises(type_name=meta.type_name, description=meta.description)
        for meta in doc.raises
        if meta.args and meta.args[0] in ParsingSettings.RAISES_KEYS
    )
    return ParsedDocstring(style=style, params=params, returns=returns, raises=raises)


def _best_style(text: str) -> DocstringStyle | None:
    best: DocstringStyle | None = None
    best_score = 0
    for style in ParsingSettings.LIBRARY_STYLES:
        doc = _parse(text, style)
        score = len(doc.meta) if doc is not None else 0
        if score > best_score:
            best, best_score = style, score
    return best


def _parse(text: str, style: DocstringStyle) -> Docstring | None:
    try:
        return parse(text, ParsingSettings.LIBRARY_STYLES[style])
    except ParseError:
        return None
