"""Projects: registered local git repositories, and their API schemas."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlmodel import Field, SQLModel

from backend.models.types import UTCDateTime, utcnow


class IndexStatus(StrEnum):
    """Lifecycle of a project's code index."""

    IDLE = "idle"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class Project(SQLModel, table=True):
    """A local git repository registered with Docter."""

    __tablename__ = "projects"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    path: str = Field(unique=True)
    current_branch: str | None = None
    index_status: IndexStatus = IndexStatus.IDLE
    indexed_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    created_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)


class ProjectCreate(BaseModel):
    """Request body of ``POST /api/projects``."""

    model_config = ConfigDict(extra="forbid")

    path: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4096)]


class ProjectRead(BaseModel):
    """A registered project as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    path: str
    current_branch: str | None
    index_status: IndexStatus
    indexed_at: datetime | None
    created_at: datetime


class ProjectCreated(ProjectRead):
    """Response body of ``POST /api/projects``: the project plus working-tree warnings."""

    is_dirty: bool
    warnings: list[str]
