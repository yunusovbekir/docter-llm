"""Tests for docstring style detection and section parsing."""

from pathlib import Path

import pytest

from docter_mcp.parsing import (
    DEFAULT_STYLE,
    DocParam,
    DocRaises,
    DocReturns,
    DocstringStyle,
    ParsedDocstring,
    PythonParser,
    detect_style,
    parse_docstring,
)

GOOGLE = """Summary.

Args:
    x (int): The x.

Returns:
    str: The result.
"""

NUMPY = """Summary.

Parameters
----------
x : int
    The x.
"""

REST = """Summary.

:param x: The x.
:type x: int
:returns: The result.
:rtype: str
:raises ValueError: If x is negative.
"""


@pytest.fixture(scope="module")
def docstrings(sample_repo: Path) -> dict[str, str]:
    """Docstrings of the sample repo, by qualified name."""
    parser = PythonParser()
    result: dict[str, str] = {}
    for file in sorted(sample_repo.rglob("*.py")):
        path = file.relative_to(sample_repo).as_posix()
        for symbol in parser.parse(path, file.read_text(encoding="utf-8")).symbols:
            if symbol.docstring is not None:
                result[symbol.qualname] = symbol.docstring
    return result


def test_detects_google_as_the_dominant_style_of_the_sample_repo(
    docstrings: dict[str, str],
) -> None:
    assert detect_style(docstrings.values()) is DocstringStyle.GOOGLE


@pytest.mark.parametrize(
    ("texts", "expected"),
    [
        ([NUMPY], DocstringStyle.NUMPY),
        ([REST, REST, GOOGLE], DocstringStyle.REST),
        ([GOOGLE, NUMPY], DocstringStyle.GOOGLE),  # tie: google wins
        ([NUMPY, REST], DocstringStyle.NUMPY),  # tie: numpy beats rest
    ],
)
def test_detect_style(texts: list[str], expected: DocstringStyle) -> None:
    assert detect_style(texts) is expected


@pytest.mark.parametrize("texts", [[], [None, ""], ["Just a summary.", "No sections here."]])
def test_detect_style_defaults_to_google_without_sections(texts: list[str | None]) -> None:
    assert detect_style(texts) is DEFAULT_STYLE is DocstringStyle.GOOGLE


def test_detect_style_ignores_malformed_docstrings() -> None:
    assert detect_style([":param: no name", NUMPY]) is DocstringStyle.NUMPY


def test_parse_google_docstring_from_the_sample_repo(docstrings: dict[str, str]) -> None:
    parsed = parse_docstring(docstrings["mylib.client.Client.send"], DocstringStyle.GOOGLE)
    assert parsed == ParsedDocstring(
        style=DocstringStyle.GOOGLE,
        params=(
            DocParam(name="data", type_name=None, description="Payload to send."),
            DocParam(name="timeout", type_name=None, description="Seconds to wait for an answer."),
            DocParam(name="retries", type_name=None, description="How often to retry."),
        ),
        returns=DocReturns(type_name=None, description="Number of bytes sent."),
        raises=(
            DocRaises(type_name="ValueError", description="If ``data`` is empty."),
            DocRaises(type_name="ClientError", description="If the service rejects the payload."),
        ),
    )


def test_google_variadic_params_keep_their_stars(docstrings: dict[str, str]) -> None:
    parsed = parse_docstring(docstrings["mylib.client.Client.fetch"], DocstringStyle.GOOGLE)
    assert [p.name for p in parsed.params] == ["path", "*segments", "**headers"]


def test_drifted_docstring_exposes_the_documented_names(docstrings: dict[str, str]) -> None:
    from_url = parse_docstring(docstrings["mylib.client.Client.from_url"], DocstringStyle.GOOGLE)
    assert [p.name for p in from_url.params] == ["address"]
    connect = parse_docstring(docstrings["mylib.client.connect"], DocstringStyle.GOOGLE)
    assert [p.name for p in connect.params] == ["host", "port"]


def test_parse_numpy_docstring_from_the_sample_repo(docstrings: dict[str, str]) -> None:
    parsed = parse_docstring(docstrings["mylib.legacy.mean"], DocstringStyle.NUMPY)
    assert parsed.params == (
        DocParam(name="values", type_name="list of float", description="Numbers to average."),
    )
    assert parsed.returns == DocReturns(type_name="float", description="The mean of ``values``.")
    assert parsed.raises == (
        DocRaises(type_name="ZeroDivisionError", description="If ``values`` is empty."),
    )


def test_parse_rest_docstring() -> None:
    parsed = parse_docstring(REST, DocstringStyle.REST)
    assert parsed.params == (DocParam(name="x", type_name="int", description="The x."),)
    assert parsed.returns == DocReturns(type_name="str", description="The result.")
    assert parsed.raises == (DocRaises(type_name="ValueError", description="If x is negative."),)


def test_attributes_are_not_params() -> None:
    text = "Summary.\n\nArgs:\n    x: The x.\n\nAttributes:\n    y: The y.\n"
    parsed = parse_docstring(text, DocstringStyle.GOOGLE)
    assert [p.name for p in parsed.params] == ["x"]


def test_yields_are_not_returns() -> None:
    text = "Summary.\n\nYields:\n    int: The next value.\n"
    assert parse_docstring(text, DocstringStyle.GOOGLE).returns is None


def test_numpy_warns_are_not_raises() -> None:
    text = "Summary.\n\nWarns\n-----\nUserWarning\n    If deprecated.\n"
    assert parse_docstring(text, DocstringStyle.NUMPY).raises == ()


def test_docstring_in_another_style_yields_empty_sections() -> None:
    assert parse_docstring(GOOGLE, DocstringStyle.NUMPY) == ParsedDocstring(
        style=DocstringStyle.NUMPY
    )


def test_malformed_docstring_yields_empty_sections() -> None:
    assert parse_docstring(":param: no name", DocstringStyle.REST) == ParsedDocstring(
        style=DocstringStyle.REST
    )
