"""Shared source parsing conventions and docstring defaults."""

import ast
import re
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar

from docstring_parser import DocstringStyle as LibraryStyle


class DocstringStyle(StrEnum):
    """A docstring convention with structured sections."""

    GOOGLE = "google"
    NUMPY = "numpy"
    REST = "rest"


class ParsingSettings:
    """Fixed parser conventions, shared without mutable module-level state."""

    DEFAULT_STYLE: ClassVar[DocstringStyle] = DocstringStyle.GOOGLE
    # Iteration order is also the tie-break order.
    LIBRARY_STYLES: ClassVar[MappingProxyType[DocstringStyle, LibraryStyle]] = MappingProxyType(
        {
            DocstringStyle.GOOGLE: LibraryStyle.GOOGLE,
            DocstringStyle.NUMPY: LibraryStyle.NUMPYDOC,
            DocstringStyle.REST: LibraryStyle.REST,
        }
    )
    PARAM_KEYS: ClassVar[frozenset[str]] = frozenset(
        {"param", "parameter", "arg", "argument", "key", "keyword", "other_param"}
    )
    RAISES_KEYS: ClassVar[frozenset[str]] = frozenset({"raises", "raise", "except", "exception"})
    DEFINITION_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    SCOPE_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
    PROPERTY_DECORATORS: ClassVar[frozenset[str]] = frozenset(
        {"property", "cached_property", "functools.cached_property"}
    )
    PROPERTY_ACCESSORS: ClassVar[frozenset[str]] = frozenset({"getter", "setter", "deleter"})
    INSTANCE_NAMES: ClassVar[frozenset[str]] = frozenset({"self", "cls"})
    # Same line breaks as ast: \r\n, \r, and \n (not \f or Unicode separators).
    LINE_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$")
