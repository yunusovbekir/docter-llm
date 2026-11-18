"""Shared, disconnect-aware SSE transport for chat and revision runs."""

from collections.abc import AsyncIterator

import anyio
from sse_starlette import EventSourceResponse, ServerSentEvent
from starlette.background import BackgroundTask

from backend.services.sessions import ChatRun, SessionService


def chat_stream(service: SessionService, run: ChatRun) -> EventSourceResponse:
    """Stream ordered agent events and stop the exact worker on disconnect."""

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
