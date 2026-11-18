"""Persisted conversation messages, including correlated tool calls."""

from datetime import datetime

from pydantic import JsonValue
from sqlalchemy import JSON, Text
from sqlmodel import Field, SQLModel

from backend.models.types import UTCDateTime, utcnow


class Message(SQLModel, table=True):
    """A user, assistant, system, or tool message belonging to a session."""

    __tablename__ = "messages"

    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="sessions.id", index=True)
    role: str
    content: str | None = Field(default=None, sa_type=Text)
    tool_calls: list[dict[str, JsonValue]] | None = Field(default=None, sa_type=JSON)
    tool_call_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
