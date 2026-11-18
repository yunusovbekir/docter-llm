"""Persisted review feedback on a proposal or one of its hunks."""

from datetime import datetime

from sqlalchemy import Text
from sqlmodel import Field, SQLModel

from backend.models.types import UTCDateTime, utcnow


class Comment(SQLModel, table=True):
    """A review comment retained with the original, immutable proposal."""

    __tablename__ = "comments"

    id: int | None = Field(default=None, primary_key=True)
    change_id: int = Field(foreign_key="changes.id", index=True)
    hunk_index: int | None = None
    text: str = Field(sa_type=Text)
    created_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
