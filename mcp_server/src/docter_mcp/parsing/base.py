"""Language-independent parsing interface.

A parser turns the text of one source file into contract ``GetSymbolOutput`` symbols. Parsers
are pure: they never touch the filesystem, so callers read files (through the sandbox) and pass
in the repo-relative path and the decoded source text.
"""

from dataclasses import dataclass
from typing import Protocol

from docter_contract import GetSymbolOutput


@dataclass(frozen=True, slots=True)
class ParseWarning:
    """A problem that made the parser skip a file."""

    path: str
    line: int | None
    message: str


@dataclass(frozen=True, slots=True)
class ParseResult:
    """Symbols extracted from a file, in source order, and the warnings raised on the way."""

    symbols: tuple[GetSymbolOutput, ...] = ()
    warnings: tuple[ParseWarning, ...] = ()


class LanguageParser(Protocol):
    """Extracts symbols from source files of one programming language."""

    @property
    def language(self) -> str:
        """Name of the language, for example ``"python"``."""
        ...

    @property
    def extensions(self) -> frozenset[str]:
        """File extensions the parser handles, including the dot, for example ``".py"``."""
        ...

    def parse(self, path: str, source: str) -> ParseResult:
        """Extract the symbols of one file.

        ``path`` is relative to the repository root and uses ``/`` separators. A file that
        cannot be parsed yields no symbols and a ``ParseWarning``; this method never raises
        for bad input.
        """
        ...
