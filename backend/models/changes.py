"""Diff-viewer response schemas, separate from the change persistence tables."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from backend.models.change import ChangeStatus


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


class ChangeSetRead(BaseModel):
    """One agent run and its chronologically ordered proposals."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: int
    run_id: str
    created_at: datetime
    changes: list[ChangeRead]
