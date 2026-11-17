"""Tests for the ast-based Python parser."""

from pathlib import Path

import pytest

from docter_contract import GetSymbolOutput, ParameterKind, SymbolKind, SymbolParameter
from docter_mcp.parsing import (
    LanguageParser,
    ParseResult,
    ParseWarning,
    PythonParser,
    format_parameters,
    is_private_name,
    module_name,
)

PARSER = PythonParser()
CLIENT_PATH = "src/mylib/client.py"


def parse(source: str, path: str = "pkg/mod.py") -> dict[str, GetSymbolOutput]:
    result = PARSER.parse(path, source)
    assert result.warnings == ()
    return {symbol.qualname: symbol for symbol in result.symbols}


@pytest.fixture(scope="module")
def parsed(sample_repo: Path) -> ParseResult:
    symbols: list[GetSymbolOutput] = []
    warnings: list[ParseWarning] = []
    for file in sorted(sample_repo.rglob("*.py")):
        path = file.relative_to(sample_repo).as_posix()
        result = PARSER.parse(path, file.read_text(encoding="utf-8"))
        symbols.extend(result.symbols)
        warnings.extend(result.warnings)
    return ParseResult(symbols=tuple(symbols), warnings=tuple(warnings))


@pytest.fixture(scope="module")
def symbols(parsed: ParseResult) -> dict[str, GetSymbolOutput]:
    return {symbol.qualname: symbol for symbol in parsed.symbols}


@pytest.fixture(scope="module")
def client_lines(sample_repo: Path) -> list[str]:
    return (sample_repo / CLIENT_PATH).read_text(encoding="utf-8").splitlines(keepends=True)


def test_parser_satisfies_language_parser_protocol() -> None:
    parser: LanguageParser = PythonParser()
    assert parser.language == "python"
    assert parser.extensions == frozenset({".py"})


def test_extracts_every_symbol_of_the_sample_repo(symbols: dict[str, GetSymbolOutput]) -> None:
    assert {name: symbol.kind for name, symbol in symbols.items()} == {
        "mylib": SymbolKind.MODULE,
        "mylib.configure": SymbolKind.FUNCTION,
        "mylib._internal": SymbolKind.MODULE,
        "mylib._internal.retry": SymbolKind.FUNCTION,
        "mylib.client": SymbolKind.MODULE,
        "mylib.client.ClientError": SymbolKind.CLASS,
        "mylib.client.Client": SymbolKind.CLASS,
        "mylib.client.Client.__init__": SymbolKind.METHOD,
        "mylib.client.Client.name": SymbolKind.PROPERTY,
        "mylib.client.Client.address": SymbolKind.PROPERTY,
        "mylib.client.Client.send": SymbolKind.METHOD,
        "mylib.client.Client.fetch": SymbolKind.METHOD,
        "mylib.client.Client.parse_url": SymbolKind.METHOD,
        "mylib.client.Client.from_url": SymbolKind.METHOD,
        "mylib.client.Client._write": SymbolKind.METHOD,
        "mylib.client.Client.Options": SymbolKind.CLASS,
        "mylib.client.Client.Options.as_dict": SymbolKind.METHOD,
        "mylib.client.connect": SymbolKind.FUNCTION,
        "mylib.client.ping": SymbolKind.FUNCTION,
        "mylib.client._helper": SymbolKind.FUNCTION,
        "mylib.client.Undocumented": SymbolKind.CLASS,
        "mylib.legacy": SymbolKind.MODULE,
        "mylib.legacy.mean": SymbolKind.FUNCTION,
    }


def test_symbols_are_in_source_order(parsed: ParseResult) -> None:
    client = [s for s in parsed.symbols if s.file == CLIENT_PATH]
    assert [s.start_line for s in client] == sorted(s.start_line for s in client)


def test_syntax_error_is_skipped_with_a_warning(parsed: ParseResult) -> None:
    assert len(parsed.warnings) == 1
    warning = parsed.warnings[0]
    assert warning.path == "src/mylib/broken.py"
    assert warning.line == 4
    assert warning.message.startswith("Skipped: invalid Python syntax")
    assert not any(s.file == "src/mylib/broken.py" for s in parsed.symbols)


def test_module_symbol(
    symbols: dict[str, GetSymbolOutput], sample_repo: Path, client_lines: list[str]
) -> None:
    module = symbols["mylib.client"]
    assert module.file == CLIENT_PATH
    assert (module.start_line, module.end_line) == (1, len(client_lines))
    assert module.signature == ""
    assert module.parameters == []
    assert module.docstring == "Client for talking to a remote service."
    assert module.source == (sample_repo / CLIENT_PATH).read_text(encoding="utf-8")


def test_method_signature_and_parameters(symbols: dict[str, GetSymbolOutput]) -> None:
    send = symbols["mylib.client.Client.send"]
    assert send.signature == (
        "def send(self, data: bytes, /, *, timeout: float = DEFAULT_TIMEOUT, retries: int = 3)"
        " -> int"
    )
    assert send.parameters == [
        SymbolParameter(name="self", kind=ParameterKind.POSITIONAL_ONLY),
        SymbolParameter(name="data", annotation="bytes", kind=ParameterKind.POSITIONAL_ONLY),
        SymbolParameter(
            name="timeout",
            annotation="float",
            default="DEFAULT_TIMEOUT",
            kind=ParameterKind.KEYWORD_ONLY,
        ),
        SymbolParameter(
            name="retries", annotation="int", default="3", kind=ParameterKind.KEYWORD_ONLY
        ),
    ]
    assert send.returns == "int"


def test_async_method_with_variadic_parameters(symbols: dict[str, GetSymbolOutput]) -> None:
    fetch = symbols["mylib.client.Client.fetch"]
    assert fetch.signature == (
        "async def fetch(self, path: str, *segments: str, **headers: Any) -> bytes"
    )
    assert [(p.name, p.kind) for p in fetch.parameters] == [
        ("self", ParameterKind.POSITIONAL_OR_KEYWORD),
        ("path", ParameterKind.POSITIONAL_OR_KEYWORD),
        ("segments", ParameterKind.VAR_POSITIONAL),
        ("headers", ParameterKind.VAR_KEYWORD),
    ]


def test_unannotated_function(symbols: dict[str, GetSymbolOutput]) -> None:
    ping = symbols["mylib.client.ping"]
    assert ping.signature == "def ping(host, timeout=None)"
    assert ping.parameters == [
        SymbolParameter(name="host", kind=ParameterKind.POSITIONAL_OR_KEYWORD),
        SymbolParameter(name="timeout", default="None", kind=ParameterKind.POSITIONAL_OR_KEYWORD),
    ]
    assert ping.returns is None
    assert ping.docstring is None


def test_class_symbols(symbols: dict[str, GetSymbolOutput]) -> None:
    assert symbols["mylib.client.ClientError"].signature == "class ClientError(Exception)"
    client = symbols["mylib.client.Client"]
    assert client.signature == "class Client"
    assert client.parameters == []
    assert client.returns is None
    assert client.raises == []
    assert client.docstring is not None
    assert client.docstring.startswith("A connection to a remote service.")


def test_decorators_and_start_line(
    symbols: dict[str, GetSymbolOutput], client_lines: list[str]
) -> None:
    assert symbols["mylib.client.Client.parse_url"].decorators == ["staticmethod"]
    assert symbols["mylib.client.Client.from_url"].decorators == ["classmethod"]
    address = symbols["mylib.client.Client.address"]
    assert address.decorators == ["functools.cached_property"]
    assert client_lines[address.start_line - 1].strip() == "@functools.cached_property"


def test_decorators_are_listed_outermost_first() -> None:
    source = "@outer\n@middle(1)\n@pkg.inner\ndef f(): pass\n"
    assert parse(source)["pkg.mod.f"].decorators == ["outer", "middle(1)", "pkg.inner"]


def test_property_setter_is_folded_into_the_property(
    symbols: dict[str, GetSymbolOutput],
) -> None:
    name = symbols["mylib.client.Client.name"]
    assert name.kind is SymbolKind.PROPERTY
    assert name.signature == "def name(self) -> str"
    assert name.decorators == ["property"]
    assert name.docstring == "The display name of the client."
    assert "setter" not in name.source


def test_raises(symbols: dict[str, GetSymbolOutput]) -> None:
    assert symbols["mylib.client.Client.send"].raises == ["ValueError", "ClientError"]
    # Raised only by a nested function.
    assert symbols["mylib.client.Client.fetch"].raises == []
    # Re-raises an `except ... as exc` alias.
    assert symbols["mylib.client.Client._write"].raises == []


def test_raise_forms() -> None:
    source = (
        "def f(self):\n"
        "    raise errors.NotFound('x')\n"
        "    raise KeyError\n"
        "    raise KeyError('again')\n"
        "    raise self.error()\n"
        "    raise\n"
        "    lambda: (_ for _ in ()).throw(OSError)\n"
        "    class Inner:\n"
        "        def g(self):\n"
        "            raise TypeError\n"
    )
    assert parse(source)["pkg.mod.f"].raises == ["errors.NotFound", "KeyError"]


def test_docstrings_are_cleaned(symbols: dict[str, GetSymbolOutput]) -> None:
    docstring = symbols["mylib.client.Client.send"].docstring
    assert docstring is not None
    assert docstring.startswith("Send data to the service.\n\nArgs:\n    data: Payload to send.")
    assert symbols["mylib.client.Client.parse_url"].docstring is None


def test_source_matches_line_range(parsed: ParseResult, client_lines: list[str]) -> None:
    for symbol in parsed.symbols:
        if symbol.file == CLIENT_PATH and symbol.kind is not SymbolKind.MODULE:
            expected = "".join(client_lines[symbol.start_line - 1 : symbol.end_line])
            assert symbol.source == expected, symbol.qualname
            first = symbol.source.lstrip()
            assert first.startswith(("@", "def ", "async def ", "class ")), symbol.qualname


@pytest.mark.parametrize(
    ("qualname", "public"),
    [
        ("mylib", True),
        ("mylib.configure", False),  # not in __all__
        ("mylib._internal", False),
        ("mylib._internal.retry", False),  # inside a private module
        ("mylib.client.Client", True),
        ("mylib.client.Client.__init__", True),  # dunders are public
        ("mylib.client.Client._write", False),
        ("mylib.client.Client.Options.as_dict", True),
        ("mylib.client._helper", False),
        ("mylib.legacy.mean", True),
    ],
)
def test_is_public(symbols: dict[str, GetSymbolOutput], qualname: str, public: bool) -> None:
    assert symbols[qualname].is_public is public


def test_children_of_private_classes_are_private() -> None:
    symbols = parse("class _Hidden:\n    def visible(self): pass\n")
    assert symbols["pkg.mod._Hidden.visible"].is_public is False


def test_static_all_variants() -> None:
    source = (
        "__all__: tuple[str, ...] = ('a', '_c')\ndef a(): pass\ndef b(): pass\ndef _c(): pass\n"
    )
    symbols = parse(source)
    assert symbols["pkg.mod.a"].is_public is True
    assert symbols["pkg.mod.b"].is_public is False
    assert symbols["pkg.mod._c"].is_public is True


def test_dynamic_all_falls_back_to_underscore_rule() -> None:
    symbols = parse("__all__ = names()\ndef a(): pass\ndef _b(): pass\n")
    assert symbols["pkg.mod.a"].is_public is True
    assert symbols["pkg.mod._b"].is_public is False


def test_empty_file() -> None:
    result = PARSER.parse("pkg/__init__.py", "")
    assert result.warnings == ()
    (module,) = result.symbols
    assert (module.qualname, module.start_line, module.end_line) == ("pkg", 1, 1)
    assert module.source == ""
    assert module.docstring is None


@pytest.mark.parametrize("source", ["x = 1\0\n", "x = '\ud800'\n"])
def test_unparseable_text_is_skipped_with_a_warning(source: str) -> None:
    result = PARSER.parse("pkg/mod.py", source)
    assert result.symbols == ()
    assert len(result.warnings) == 1
    assert result.warnings[0].message.startswith("Skipped:")


def test_nested_functions_are_not_symbols() -> None:
    symbols = parse("def outer():\n    def inner(): pass\n    class Local: pass\n")
    assert set(symbols) == {"pkg.mod", "pkg.mod.outer"}


def test_conditional_definitions_and_overloads() -> None:
    source = (
        "from typing import overload\n"
        "if TYPE_CHECKING:\n"
        "    def checked(): pass\n"
        "try:\n"
        "    def fast(): pass\n"
        "except ImportError:\n"
        "    def slow(): pass\n"
        "@overload\n"
        "def f(x: int) -> int: ...\n"
        "@overload\n"
        "def f(x: str) -> str: ...\n"
        "def f(x):\n"
        "    return x\n"
    )
    symbols = parse(source)
    assert {"pkg.mod.checked", "pkg.mod.fast", "pkg.mod.slow"} <= set(symbols)
    assert symbols["pkg.mod.f"].signature == "def f(x)"
    assert symbols["pkg.mod.f"].decorators == []


def test_signature_markers() -> None:
    symbols = parse("def f(a, /, b, *args, c, d=1, **kw): pass\ndef g(*, key): pass\n")
    assert symbols["pkg.mod.f"].signature == "def f(a, /, b, *args, c, d=1, **kw)"
    assert symbols["pkg.mod.g"].signature == "def g(*, key)"


def test_type_parameters_and_class_keywords() -> None:
    source = (
        "def first[T](items: list[T]) -> T: ...\nclass Box[T: int](Base, metaclass=Meta): pass\n"
    )
    symbols = parse(source)
    assert symbols["pkg.mod.first"].signature == "def first[T](items: list[T]) -> T"
    assert symbols["pkg.mod.Box"].signature == "class Box[T: int](Base, metaclass=Meta)"


def test_multiline_annotation_is_written_on_one_line() -> None:
    symbols = parse("def f(x: dict[\n    str,\n    int,\n]) -> None: ...\n")
    assert symbols["pkg.mod.f"].parameters[0].annotation == "dict[str, int]"


def test_non_ascii_source_segments() -> None:
    symbols = parse('def f(name: "Ŧype" = "ünïcode") -> "Ŧ": ...\n')
    (parameter,) = symbols["pkg.mod.f"].parameters
    assert (parameter.annotation, parameter.default) == ('"Ŧype"', '"ünïcode"')
    assert symbols["pkg.mod.f"].returns == '"Ŧ"'


def test_crlf_line_endings() -> None:
    symbols = parse("def f():\r\n    pass\r\n\r\ndef g(): pass\r\n")
    g = symbols["pkg.mod.g"]
    assert (g.start_line, g.end_line) == (4, 4)
    assert g.source == "def g(): pass\r\n"
    assert symbols["pkg.mod"].end_line == 4


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/mylib/client.py", "mylib.client"),
        ("src/mylib/__init__.py", "mylib"),
        ("mylib/sub/mod.py", "mylib.sub.mod"),
        ("setup.py", "setup"),
        ("__init__.py", "__init__"),
    ],
)
def test_module_name(path: str, expected: str) -> None:
    assert module_name(path) == expected


@pytest.mark.parametrize(
    ("name", "private"),
    [("public", False), ("_private", True), ("__mangled", True), ("__init__", False), ("_", True)],
)
def test_is_private_name(name: str, private: bool) -> None:
    assert is_private_name(name) is private


def test_dunder_module_is_public() -> None:
    assert parse("", path="pkg/__main__.py")["pkg.__main__"].is_public is True


def test_format_parameters_round_trips_symbol_parameters() -> None:
    symbols = parse("def f(a: int = 1, /, *b: str, c, **d: object): pass\n")
    parameters = symbols["pkg.mod.f"].parameters
    assert format_parameters(parameters) == "a: int = 1, /, *b: str, c, **d: object"
