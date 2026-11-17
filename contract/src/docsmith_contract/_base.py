"""Base model and reusable field types shared by every contract model."""

from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

LANGUAGE_TAG_PATTERN = r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$"
"""BCP 47 language tag, for example ``de``, ``pt-BR``, or ``zh-Hans``."""


class ContractModel(BaseModel):
    """Base class for contract models. Unknown fields are rejected and instances are immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def _require_absolute(value: str) -> str:
    if not (PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute()):
        raise ValueError("must be an absolute path")
    return value


AbsolutePath = Annotated[str, Field(min_length=1), AfterValidator(_require_absolute)]
"""An absolute filesystem path (POSIX or Windows)."""

RepoRelativePath = Annotated[
    str,
    Field(min_length=1, description="Path relative to the repository root, with '/' separators."),
]
"""A file path relative to the repository root."""

PathPrefix = Annotated[
    str,
    Field(
        min_length=1,
        description="Only consider files whose repo-relative path starts with this prefix, "
        "for example 'src/mylib/'.",
    ),
]
"""A repo-relative path prefix used to narrow a scan."""

LineNumber = Annotated[int, Field(ge=1, description="1-based line number.")]
"""A 1-based line number."""

Count = Annotated[int, Field(ge=0)]
"""A non-negative integer count."""

LanguageTag = Annotated[
    str,
    Field(
        pattern=LANGUAGE_TAG_PATTERN,
        description="BCP 47 language tag, for example 'de', 'pt-BR', or 'zh-Hans'.",
    ),
]
"""A BCP 47 language tag."""


def check_line_range(start_line: int | None, end_line: int | None) -> None:
    """Raise ``ValueError`` if both lines are set and ``end_line`` precedes ``start_line``."""
    if start_line is not None and end_line is not None and end_line < start_line:
        raise ValueError("end_line must be greater than or equal to start_line")


class RepoInput(ContractModel):
    """Base class for tool inputs. Every tool that works on a repository takes ``repo``."""

    repo: AbsolutePath = Field(description="Absolute path to the root of the local git repository.")
