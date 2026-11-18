"""Session creation, completed history, and disconnect-aware chat streams."""

from typing import Annotated

from fastapi import APIRouter, Query, status
from sse_starlette import EventSourceResponse

from backend.api.deps import ReviewServiceDep, SessionServiceDep
from backend.api.streams import chat_stream
from backend.models.changes import ChangeRead, ChangeSetRead
from backend.models.chat import (
    ChatRequest,
    SessionCreate,
    SessionDetail,
    SessionRead,
    SessionSummary,
)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_session(data: SessionCreate, service: SessionServiceDep) -> SessionRead:
    """Create a session after the project's index has finished successfully."""
    return await service.create(data)


@router.get("/{session_id}")
async def get_session(session_id: int, service: SessionServiceDep) -> SessionDetail:
    """Return session configuration, run state, and completed messages."""
    return await service.get(session_id)


@router.get("")
async def list_sessions(
    project_id: Annotated[int, Query(gt=0)], service: SessionServiceDep
) -> list[SessionSummary]:
    """List project sessions with pending, applied, and denied proposal counts."""
    return await service.list_all(project_id)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(session_id: int, service: ReviewServiceDep) -> None:
    """Remove a session and its worktree, retaining its Git branch."""
    await service.delete_session(session_id)


@router.post(
    "/{session_id}/chat",
    response_class=EventSourceResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def chat(
    session_id: int, data: ChatRequest, service: SessionServiceDep
) -> EventSourceResponse:
    """Stream JSON agent events, cancelling the worker on disconnect."""
    run = await service.start_chat(session_id, data.message)
    return chat_stream(service, run)


@router.get("/{session_id}/changes")
async def get_changes(session_id: int, service: SessionServiceDep) -> list[ChangeSetRead]:
    """Return change sets with nested proposals, hunks, and stable diff contents."""
    return await service.get_changes(session_id)


@router.post("/{session_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
async def cancel(session_id: int, service: SessionServiceDep) -> None:
    """Cancel the active run, if any, and wait until the session is idle."""
    await service.cancel(session_id)


@router.post("/{session_id}/changes/approve-all")
async def approve_all(session_id: int, service: ReviewServiceDep) -> list[ChangeRead]:
    """Apply remaining proposals in order, without overriding prior review decisions."""
    return await service.approve_all(session_id)
