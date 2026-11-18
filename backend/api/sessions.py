"""Session creation, completed history, and disconnect-aware chat streams."""

from collections.abc import AsyncIterator

import anyio
from fastapi import APIRouter, status
from sse_starlette import EventSourceResponse, ServerSentEvent
from starlette.background import BackgroundTask

from backend.api.deps import SessionServiceDep
from backend.models.changes import ChangeSetRead
from backend.models.chat import ChatRequest, SessionCreate, SessionDetail, SessionRead

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_session(data: SessionCreate, service: SessionServiceDep) -> SessionRead:
    """Create a session after the project's index has finished successfully."""
    return await service.create(data)


@router.get("/{session_id}")
async def get_session(session_id: int, service: SessionServiceDep) -> SessionDetail:
    """Return session configuration, run state, and completed messages."""
    return await service.get(session_id)


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

    async def cleanup() -> None:
        with anyio.CancelScope(shield=True):
            await service.stop(run)
            await run.events.aclose()

    async def stream() -> AsyncIterator[ServerSentEvent]:
        try:
            sequence = 0
            async for event in run.events:
                sequence += 1
                yield ServerSentEvent(
                    event=event.type, data=event.model_dump_json(), id=f"{run.id}:{sequence}"
                )
        finally:
            await cleanup()

    return EventSourceResponse(stream(), background=BackgroundTask(cleanup))


@router.get("/{session_id}/changes")
async def get_changes(session_id: int, service: SessionServiceDep) -> list[ChangeSetRead]:
    """Return change sets with nested proposals, hunks, and stable diff contents."""
    return await service.get_changes(session_id)


@router.post("/{session_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
async def cancel(session_id: int, service: SessionServiceDep) -> None:
    """Cancel the active run, if any, and wait until the session is idle."""
    await service.cancel(session_id)
