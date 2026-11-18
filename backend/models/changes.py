"""Diff-viewer response schemas, separate from the change persistence tables."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints

from backend.models.change import ChangeStatus


class HunkSelection(BaseModel):
    """Select zero-based hunks, or omit the selection to review all remaining hunks."""

    model_config = ConfigDict(extra="forbid")

    hunks: list[Annotated[StrictInt, Field(ge=0)]] | None = None


class CommentCreate(BaseModel):
    """Nonblank feedback on an entire proposal or a single zero-based hunk."""

    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
    hunk_index: Annotated[StrictInt, Field(ge=0)] | None = None


class CommentRead(BaseModel):
    """Persisted review feedback, including its scope and UTC timestamp."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    change_id: int
    hunk_index: int | None
    text: str
    created_at: datetime


class ChangeHunkRead(BaseModel):
    """A persisted hunk's position, header, and review status."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    change_id: int
    index: int
    header: str
    status: ChangeStatus


class ChangeRead(BaseModel):
    """A proposal and stable snapshots suitable for a side-by-side diff."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    change_set_id: int
    file_path: str
    patch: str
    rationale: str
    status: ChangeStatus
    parent_change_id: int | None
    created_at: datetime
    original_content: str
    proposed_content: str
    hunks: list[ChangeHunkRead]
    comments: list[CommentRead]


class ChangeSetRead(BaseModel):
    """One agent run and its chronologically ordered proposals."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: int
    run_id: str
    created_at: datetime
    changes: list[ChangeRead]
