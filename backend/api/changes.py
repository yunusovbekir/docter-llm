"""Thin review endpoints for proposals and streamed agent revisions."""

from typing import Annotated

from fastapi import APIRouter, Body
from sse_starlette import EventSourceResponse

from backend.api.deps import ReviewServiceDep, SessionServiceDep
from backend.api.streams import chat_stream
from backend.models.changes import ChangeRead, CommentCreate, HunkSelection

router = APIRouter(prefix="/api/changes", tags=["changes"])


@router.post("/{change_id}/approve")
async def approve(
    change_id: int,
    service: ReviewServiceDep,
    data: Annotated[HunkSelection, Body(default_factory=HunkSelection)],
) -> ChangeRead:
    """Apply and commit all remaining hunks, or only those explicitly selected."""
    return await service.approve(change_id, data.hunks)


@router.post("/{change_id}/deny")
async def deny(
    change_id: int,
    service: ReviewServiceDep,
    data: Annotated[HunkSelection, Body(default_factory=HunkSelection)],
) -> ChangeRead:
    """Deny all remaining hunks, or only those explicitly selected."""
    return await service.deny(change_id, data.hunks)


@router.post(
    "/{change_id}/comment",
    response_class=EventSourceResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def comment(
    change_id: int,
    data: CommentCreate,
    service: ReviewServiceDep,
    chat: SessionServiceDep,
) -> EventSourceResponse:
    """Persist feedback and stream the revision using the ordinary chat transport."""
    run = await service.comment(change_id, data)
    return chat_stream(chat, run)
