"""Persisted, run-scoped patch proposals and their reviewable hunks."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Enum as SAEnum
from sqlalchemy import Text, UniqueConstraint
from sqlmodel import Field, SQLModel

from backend.models.types import UTCDateTime, utcnow


class ChangeStatus(StrEnum):
    """Review and application states for changes and individual hunks."""

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    PARTIAL = "partial"
    APPLIED = "applied"
    CONFLICT = "conflict"
    REVISED = "revised"


class ChangeSet(SQLModel, table=True):
    """The proposals produced by one agent run, including runs with no proposals."""

    __tablename__ = "change_sets"

    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="sessions.id", index=True)
    run_id: str = Field(unique=True)
    created_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)


class Change(SQLModel, table=True):
    """A validated proposal with immutable before/after snapshots for review."""

    __tablename__ = "changes"

    id: int | None = Field(default=None, primary_key=True)
    change_set_id: int = Field(foreign_key="change_sets.id", index=True)
    file_path: str
    patch: str = Field(sa_type=Text)
    rationale: str = Field(sa_type=Text)
    status: ChangeStatus = Field(
        default=ChangeStatus.PENDING, sa_type=SAEnum(ChangeStatus, native_enum=False)
    )
    parent_change_id: int | None = Field(default=None, foreign_key="changes.id")
    created_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
    original_content: str = Field(sa_type=Text)
    proposed_content: str = Field(sa_type=Text)


class ChangeHunk(SQLModel, table=True):
    """One zero-based, ordered unified-diff hunk belonging to a change."""

    __tablename__ = "change_hunks"
    __table_args__ = (UniqueConstraint("change_id", "index"),)

    id: int | None = Field(default=None, primary_key=True)
    change_id: int = Field(foreign_key="changes.id", index=True)
    index: int
    header: str
    status: ChangeStatus = Field(
        default=ChangeStatus.PENDING, sa_type=SAEnum(ChangeStatus, native_enum=False)
    )
