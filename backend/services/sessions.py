"""Session persistence and app-scoped, cancellable streaming chat runs."""

import asyncio
import logging
from collections.abc import Mapping, Sequence
from contextlib import aclosing
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from sqlalchemy import func
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import col, delete, select, update
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.agent import Mode, Orchestrator, events
from backend.config import Settings
from backend.errors import DocterError
from backend.mcp_client import MCPManager
from backend.models import (
    AppSettings,
    Change,
    ChangeHunk,
    ChangeSet,
    Comment,
    Message,
    Project,
    Session,
)
from backend.models.change import ChangeStatus
from backend.models.changes import ChangeSetRead
from backend.models.chat import (
    ChangeCounts,
    MessageRead,
    SessionCreate,
    SessionDetail,
    SessionRead,
    SessionSummary,
)
from backend.models.project import IndexStatus
from backend.models.session import RunStatus
from backend.models.types import utcnow
from backend.services.changes import DbChangeSink, get_change, list_change_sets
from backend.services.projects import ProjectNotFoundError
from backend.services.secrets import ApiKeyStore
from backend.services.settings import SettingsService

if TYPE_CHECKING:
    from backend.git.service import GitService

logger = logging.getLogger(__name__)


class SessionNotFoundError(DocterError):
    """No session has the requested id."""

    status_code = 404
    code = "session_not_found"


class ProjectNotIndexedError(DocterError):
    """A session cannot be created until the project's index is ready."""

    status_code = 409
    code = "project_not_indexed"


class SessionRunningError(DocterError):
    """The session already has an active agent run."""

    status_code = 409
    code = "session_running"


@dataclass(frozen=True)
class ChatRun:
    """A single run's identity, bounded event receiver, and owned worker."""

    session_id: int
    id: str
    events: MemoryObjectReceiveStream[events.AgentEvent]
    task: asyncio.Task[None]
    cancel_scope: anyio.CancelScope


class SessionService:
    """Own session operations and one active worker per session for a single app."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], mcp: MCPManager) -> None:
        """Inject the database factory and the shared MCP client."""
        self._sessions = sessions
        self._mcp = mcp
        self._runs: dict[int, ChatRun] = {}
        self._lock = asyncio.Lock()

    async def recover(self) -> None:
        """Clear run flags left by a previous process before accepting requests."""
        async with self._sessions() as db:
            await db.exec(
                update(Session)
                .where(col(Session.run_status) == RunStatus.RUNNING)
                .values(run_status=RunStatus.IDLE)
            )
            await db.commit()

    async def create(self, data: SessionCreate) -> SessionRead:
        """Create an idle session only for a successfully indexed project."""
        data.mode.validate_params(data.mode_params)
        async with self._sessions() as db:
            project = await self._get_project(db, data.project_id)
            if project.index_status != IndexStatus.DONE:
                raise ProjectNotIndexedError(
                    f"Project {data.project_id} must finish indexing before creating a session"
                )
            row = Session(
                project_id=data.project_id, mode=data.mode.value, mode_params=data.mode_params
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return SessionRead.model_validate(row)

    async def get(self, session_id: int) -> SessionDetail:
        """Return persisted session state and completed messages in order."""
        async with self._sessions() as db:
            row = await self._get_row(db, session_id)
            messages = await self._messages(db, session_id)
            return SessionDetail(
                **SessionRead.model_validate(row).model_dump(),
                messages=[MessageRead.model_validate(message) for message in messages],
            )

    async def list_all(self, project_id: int | None = None) -> list[SessionSummary]:
        """List sessions oldest first, counting proposals, not hunks."""
        async with self._sessions() as db:
            sessions = select(Session).order_by(col(Session.created_at), col(Session.id))
            totals_query = (
                select(ChangeSet.session_id, Change.status, func.count(col(Change.id)))
                .join(Change, col(Change.change_set_id) == col(ChangeSet.id))
                .join(Session, col(Session.id) == col(ChangeSet.session_id))
                .group_by(col(ChangeSet.session_id), col(Change.status))
            )
            if project_id is not None:
                await self._get_project(db, project_id)
                sessions = sessions.where(Session.project_id == project_id)
                totals_query = totals_query.where(Session.project_id == project_id)
            rows = (await db.exec(sessions)).all()
            counts: dict[int, ChangeCounts] = {}
            totals = await db.exec(totals_query)
            for session_id, status, count in totals:
                summary = counts.setdefault(session_id, ChangeCounts())
                if status == ChangeStatus.PENDING:
                    summary.pending = count
                elif status == ChangeStatus.APPLIED:
                    summary.applied = count
                elif status == ChangeStatus.DENIED:
                    summary.denied = count
            result = []
            for row in rows:
                assert row.id is not None
                result.append(
                    SessionSummary(
                        **SessionRead.model_validate(row).model_dump(),
                        change_counts=counts.get(row.id, ChangeCounts()),
                    )
                )
            return result

    async def delete(self, session_id: int, git: "GitService") -> None:
        """Stop the worker, remove its clean worktree, and delete persisted history.

        The caller holds the session's review lock to exclude concurrent decisions.
        Git failures leave history intact and never force removal of user edits.
        """
        async with self._lock:
            async with self._sessions() as db:
                row = await self._get_row(db, session_id)
            run = self._runs.get(session_id)
            if run is not None:
                await self.stop(run)
            await git.remove_worktree(row)
            async with self._sessions() as db:
                change_sets = select(ChangeSet.id).where(ChangeSet.session_id == session_id)
                changes = select(Change.id).where(col(Change.change_set_id).in_(change_sets))
                await db.exec(delete(Comment).where(col(Comment.change_id).in_(changes)))
                await db.exec(delete(ChangeHunk).where(col(ChangeHunk.change_id).in_(changes)))
                await db.exec(delete(Change).where(col(Change.id).in_(changes)))
                await db.exec(delete(ChangeSet).where(col(ChangeSet.session_id) == session_id))
                await db.exec(delete(Message).where(col(Message.session_id) == session_id))
                await db.exec(delete(Session).where(col(Session.id) == session_id))
                await db.commit()
            logger.info("Removed session %s; Git branch retained", session_id)

    async def start_chat(self, session_id: int, message: str) -> ChatRun:
        """Reserve the session and persist the user before sending SSE headers."""
        return await self._start_run(session_id, message)

    async def start_revision(self, session_id: int, message: str, comment: Comment) -> ChatRun:
        """Atomically save feedback, supersede its proposal, and start a chat worker."""
        return await self._start_run(session_id, message, comment)

    async def _start_run(
        self, session_id: int, message: str, comment: Comment | None = None
    ) -> ChatRun:
        async with self._lock, self._sessions() as db:
            row = await self._get_row(db, session_id)
            if row.run_status == RunStatus.RUNNING:
                raise SessionRunningError(f"Session {session_id} already has an active run")
            project = await self._get_project(db, row.project_id)
            mode = Mode(row.mode)
            mode.validate_params(row.mode_params)
            history = _history(await self._messages(db, session_id))
            if comment is not None:
                parent = await get_change(db, comment.change_id)
                parent.status = ChangeStatus.REVISED
                db.add(comment)
            run_id = uuid4().hex
            change_set = ChangeSet(session_id=session_id, run_id=run_id)
            db.add(change_set)
            row.run_status = RunStatus.RUNNING
            db.add(Message(session_id=session_id, role="user", content=message))
            await db.commit()
            assert change_set.id is not None
            send, receive = anyio.create_memory_object_stream[events.AgentEvent](
                Settings.CHAT_EVENT_BUFFER_SIZE
            )
            cancel_scope = anyio.CancelScope()
            task = asyncio.create_task(
                self._run(
                    row,
                    project,
                    history,
                    message,
                    send,
                    cancel_scope,
                    change_set.id,
                    comment.change_id if comment is not None else None,
                ),
                name=f"docter-chat-{session_id}",
            )
            run = ChatRun(session_id, run_id, receive, task, cancel_scope)
            self._runs[session_id] = run
            task.add_done_callback(lambda finished: self._task_done(run, finished))
            return run

    async def get_changes(self, session_id: int) -> list[ChangeSetRead]:
        """Return persisted run-scoped proposals and their diff-viewer contents."""
        async with self._sessions() as db:
            await self._get_row(db, session_id)
            return await list_change_sets(db, session_id)

    async def cancel(self, session_id: int) -> None:
        """Cancel and await the session's current run; idle sessions are unchanged."""
        async with self._lock, self._sessions() as db:
            await self._get_row(db, session_id)
            run = self._runs.get(session_id)
        if run is not None:
            await self.stop(run)

    async def stop(self, run: ChatRun) -> None:
        """Stop this exact worker, never a later run on the same session."""
        if not run.task.done():
            run.cancel_scope.cancel()
        await asyncio.gather(run.task, return_exceptions=True)

    async def close(self) -> None:
        """Cancel and await workers before closing MCP and the database."""
        async with self._lock:
            runs = list(self._runs.values())
        await asyncio.gather(*(self.stop(run) for run in runs))

    async def _run(
        self,
        row: Session,
        project: Project,
        history: Sequence[Mapping[str, Any]],
        message: str,
        send: MemoryObjectSendStream[events.AgentEvent],
        cancel_scope: anyio.CancelScope,
        change_set_id: int,
        parent_change_id: int | None,
    ) -> None:
        assert row.id is not None
        session_id = row.id

        async def on_message(completed: Mapping[str, Any]) -> None:
            with anyio.CancelScope(shield=True):
                await self._save_message(session_id, completed)

        with cancel_scope:
            async with send:
                try:
                    async with self._sessions() as db:
                        settings = (
                            await db.get(AppSettings, Settings.SETTINGS_ROW_ID) or AppSettings()
                        )
                        gateway = await SettingsService(db, ApiKeyStore()).gateway()
                    orchestrator = Orchestrator(
                        gateway,
                        self._mcp,
                        DbChangeSink(
                            self._sessions, change_set_id, parent_change_id=parent_change_id
                        ),
                        max_agent_steps=settings.max_agent_steps,
                        on_message=on_message,
                    )
                    async with aclosing(
                        orchestrator.run(history, message, Mode(row.mode), row.mode_params, project)
                    ) as stream:
                        async for event in stream:
                            await send.send(event)
                except DocterError as exc:
                    await send.send(events.Error(code=exc.code, message=exc.message))
                    await send.send(events.Done(reason="error", steps=0))
                except Exception as exc:
                    logger.error("Chat failed for session %s (%s)", session_id, type(exc).__name__)
                    await send.send(
                        events.Error(code="internal_error", message="Internal server error")
                    )
                    await send.send(events.Done(reason="error", steps=0))
                finally:
                    with anyio.CancelScope(shield=True):
                        await self._finish(session_id)

    async def _save_message(self, session_id: int, completed: Mapping[str, Any]) -> None:
        async with self._sessions() as db:
            db.add(Message.model_validate({"session_id": session_id, **completed}))
            session = await self._get_row(db, session_id)
            session.updated_at = utcnow()
            await db.commit()

    async def _finish(self, session_id: int) -> None:
        async with self._sessions() as db:
            session = await self._get_row(db, session_id)
            session.run_status = RunStatus.IDLE
            await db.commit()

    def _task_done(self, run: ChatRun, task: asyncio.Task[None]) -> None:
        if self._runs.get(run.session_id) is run:
            del self._runs[run.session_id]
        if not task.cancelled() and (exc := task.exception()) is not None:
            logger.error(
                "Could not finish chat for session %s (%s)", run.session_id, type(exc).__name__
            )

    @staticmethod
    async def _get_row(db: AsyncSession, session_id: int) -> Session:
        row = await db.get(Session, session_id)
        if row is None:
            raise SessionNotFoundError(f"Session {session_id} not found")
        return row

    @staticmethod
    async def _get_project(db: AsyncSession, project_id: int) -> Project:
        project = await db.get(Project, project_id)
        if project is None:
            raise ProjectNotFoundError(f"Project {project_id} not found")
        return project

    @staticmethod
    async def _messages(db: AsyncSession, session_id: int) -> Sequence[Message]:
        result = await db.exec(
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(col(Message.created_at), col(Message.id))
        )
        return result.all()


def _history(messages: Sequence[Message]) -> list[Mapping[str, Any]]:
    """Replay only correlated tool calls if a previous run stopped mid-batch."""
    history: list[Mapping[str, Any]] = []
    for index, message in enumerate(messages):
        if message.role == "tool":
            continue
        item: dict[str, Any] = {"role": message.role, "content": message.content}
        if not message.tool_calls:
            history.append(item)
            continue
        replies: list[Message] = []
        for following in messages[index + 1 :]:
            if following.role != "tool":
                break
            replies.append(following)
        ids = {reply.tool_call_id for reply in replies}
        calls = [call for call in message.tool_calls if call["id"] in ids]
        if calls:
            item["tool_calls"] = calls
            history.append(item)
            history.extend(
                {"role": "tool", "content": reply.content, "tool_call_id": reply.tool_call_id}
                for reply in replies
            )
        elif message.content:
            history.append(item)
    return history
