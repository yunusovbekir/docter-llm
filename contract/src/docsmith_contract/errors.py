"""Error codes and the message format tools use to report them.

A tool reports an anticipated failure by raising the MCP SDK's ``ToolError`` with
``ToolErrorInfo(code=..., message=...).to_message()``, which reads ``"CODE: message"``.
The SDK prefixes it with ``"Error executing tool <name>: "`` before it reaches the
client, so ``ToolErrorInfo.parse`` finds the code anywhere in the text.
"""

import re
from enum import StrEnum
from typing import Self

from pydantic import Field

from docsmith_contract._base import ContractModel


class ErrorCode(StrEnum):
    """Machine-readable codes for anticipated tool failures."""

    REPO_NOT_FOUND = "REPO_NOT_FOUND"
    """The ``repo`` path does not exist or is not a directory."""
    NOT_A_GIT_REPO = "NOT_A_GIT_REPO"
    """The ``repo`` path is not the root of a git repository."""
    NOT_INDEXED = "NOT_INDEXED"
    """The tool needs an index and ``index_repo`` has not been run for this repository."""
    PATH_OUTSIDE_REPO = "PATH_OUTSIDE_REPO"
    """A path resolves outside the repository or into its ``.git`` directory."""
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    """A file path inside the repository does not exist."""
    SYMBOL_NOT_FOUND = "SYMBOL_NOT_FOUND"
    """No symbol with the given qualified name exists in the index."""
    EDIT_NOT_FOUND = "EDIT_NOT_FOUND"
    """An edit's ``old_str`` does not occur in the file."""
    EDIT_AMBIGUOUS = "EDIT_AMBIGUOUS"
    """An edit's ``old_str`` occurs more than once in the file."""
    EXECUTION_DISABLED = "EXECUTION_DISABLED"
    """Running code examples is disabled in the server configuration."""
    SAMPLING_UNAVAILABLE = "SAMPLING_UNAVAILABLE"
    """The tool needs MCP sampling and the client does not support it."""


_CODE_PATTERN = re.compile(
    r"(?:^|: )(?P<code>" + "|".join(re.escape(code) for code in ErrorCode) + r"): (?P<message>.+)",
    re.DOTALL,
)


class ToolErrorInfo(ContractModel):
    """An error code with a human-readable message, as carried in a tool error result."""

    code: ErrorCode
    message: str = Field(min_length=1, description="What went wrong and how to fix it.")

    def to_message(self) -> str:
        """Format the error as the ``ToolError`` message: ``"CODE: message"``."""
        return f"{self.code}: {self.message}"

    @classmethod
    def parse(cls, text: str) -> Self | None:
        """Extract the error from a tool error result's text.

        Returns ``None`` if the text carries no known error code, for example when the
        server reports an unexpected crash or an argument validation failure.
        """
        match = _CODE_PATTERN.search(text)
        if match is None:
            return None
        return cls(code=ErrorCode(match["code"]), message=match["message"])
