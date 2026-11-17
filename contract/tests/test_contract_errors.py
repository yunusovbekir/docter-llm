import pytest
from pydantic import ValidationError

from docter_contract import ErrorCode, ToolErrorInfo

EXPECTED_CODES = {
    "REPO_NOT_FOUND",
    "NOT_A_GIT_REPO",
    "NOT_INDEXED",
    "PATH_OUTSIDE_REPO",
    "FILE_NOT_FOUND",
    "SYMBOL_NOT_FOUND",
    "EDIT_NOT_FOUND",
    "EDIT_AMBIGUOUS",
    "EXECUTION_DISABLED",
    "SAMPLING_UNAVAILABLE",
}


def test_error_codes() -> None:
    assert {code.value for code in ErrorCode} == EXPECTED_CODES
    assert all(code.name == code.value for code in ErrorCode)


def test_to_message() -> None:
    info = ToolErrorInfo(code=ErrorCode.NOT_INDEXED, message="Run index_repo first.")
    assert info.to_message() == "NOT_INDEXED: Run index_repo first."


@pytest.mark.parametrize("code", list(ErrorCode))
def test_parse_round_trips_every_code(code: ErrorCode) -> None:
    info = ToolErrorInfo(code=code, message="Details: with a colon.\nAnd a second line.")
    assert ToolErrorInfo.parse(info.to_message()) == info


def test_parse_strips_the_sdk_prefix() -> None:
    text = "Error executing tool read_file: PATH_OUTSIDE_REPO: '../etc/passwd' is outside the repo."
    assert ToolErrorInfo.parse(text) == ToolErrorInfo(
        code=ErrorCode.PATH_OUTSIDE_REPO, message="'../etc/passwd' is outside the repo."
    )


@pytest.mark.parametrize(
    "text",
    [
        "Error executing tool read_file",
        "Error executing tool read_file: 1 validation error for read_file",
        "UNKNOWN_CODE: something",
        "NOT_INDEXED:",
        "XNOT_INDEXED: glued to another word",
    ],
)
def test_parse_returns_none_without_a_known_code(text: str) -> None:
    assert ToolErrorInfo.parse(text) is None


def test_message_must_not_be_empty() -> None:
    with pytest.raises(ValidationError):
        ToolErrorInfo(code=ErrorCode.FILE_NOT_FOUND, message="")
