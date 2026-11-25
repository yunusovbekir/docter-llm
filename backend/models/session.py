"""Persisted chat sessions scoped to registered projects."""

from datetime import datetime
from enum import StrEnum

from pydantic import JsonValue
from sqlalchemy import JSON
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field, SQLModel

from backend.models.types import UTCDateTime, utcnow


class RunStatus(StrEnum):
    """Whether a session currently has an active agent run."""

    IDLE = "idle"
    RUNNING = "running"


class Session(SQLModel, table=True):
    """A project's conversation and its documentation task configuration."""

    __tablename__ = "sessions"

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="projects.id", index=True)
    mode: str
    mode_params: dict[str, JsonValue] = Field(default_factory=dict, sa_type=JSON)
    branch_name: str | None = None
    run_status: RunStatus = Field(
        default=RunStatus.IDLE, sa_type=SAEnum(RunStatus, native_enum=False)
    )
    created_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
    updated_at: datetime = Field(
        default_factory=utcnow, sa_type=UTCDateTime, sa_column_kwargs={"onupdate": utcnow}
    )
