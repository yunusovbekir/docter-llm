"""Session and chat API schemas, separate from the persistence models."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints

from backend.agent.modes import Mode
from backend.models.session import RunStatus


class SessionCreate(BaseModel):
    """Request to start a conversation for an indexed project."""

    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(gt=0)
    mode: Mode
    mode_params: dict[str, JsonValue] = Field(default_factory=dict)


class MessageRead(BaseModel):
    """A completed, persisted conversation message."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: int
    role: str
    content: str | None
    tool_calls: list[dict[str, JsonValue]] | None
    tool_call_id: str | None
    created_at: datetime


class SessionRead(BaseModel):
    """Persisted session configuration and run state."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    mode: Mode
    mode_params: dict[str, JsonValue]
    branch_name: str | None
    run_status: RunStatus
    created_at: datetime
    updated_at: datetime


class SessionDetail(SessionRead):
    """A session together with its chronologically ordered messages."""

    messages: list[MessageRead]


class ChangeCounts(BaseModel):
    """Proposal counts for the three listed review statuses."""

    pending: int = 0
    applied: int = 0
    denied: int = 0


class SessionSummary(SessionRead):
    """A listed session with its persisted proposal counts."""

    change_counts: ChangeCounts


class ChatRequest(BaseModel):
    """A nonblank user message; preserve its original whitespace."""

    model_config = ConfigDict(extra="forbid")

    message: Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
