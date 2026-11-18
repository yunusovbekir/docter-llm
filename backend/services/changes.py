"""Durable change sinks and ordered diff-viewer queries."""

import asyncio
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.agent.changes import Error, Ok
from backend.git.patches import InvalidPatchError, validate_patch
from backend.models import Change, ChangeHunk, ChangeSet, Project, Session
from backend.models.changes import ChangeHunkRead, ChangeRead, ChangeSetRead
from docter_contract import ProposePatchOutput


class DbChangeSink:
    """Validate and atomically persist proposals for one session's agent run."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], change_set_id: int) -> None:
        """Bind the sink to a previously persisted change set."""
        self._sessions = sessions
        self._change_set_id = change_set_id

    async def add(self, change: ProposePatchOutput) -> Ok | Error:
        """Persist a safe, applicable proposal or give the agent a retryable error."""
        async with self._sessions() as db:
            change_set = await db.get(ChangeSet, self._change_set_id)
            if change_set is None:
                return Error("The agent run's change set no longer exists.")
            session = await db.get(Session, change_set.session_id)
            project = await db.get(Project, session.project_id) if session is not None else None
            if project is None:
                return Error("The proposal's session or project no longer exists.")
            try:
                validated = await asyncio.to_thread(validate_patch, Path(project.path), change)
            except InvalidPatchError as exc:
                return Error(exc.message)
            row = Change(
                change_set_id=self._change_set_id,
                file_path=validated.file_path,
                patch=change.patch,
                rationale=change.rationale,
                original_content=validated.original_content,
                proposed_content=validated.proposed_content,
            )
            db.add(row)
            await db.flush()
            assert row.id is not None
            db.add_all(
                ChangeHunk(change_id=row.id, index=index, header=header)
                for index, header in enumerate(validated.headers)
            )
            await db.commit()
        return Ok()


async def list_change_sets(db: AsyncSession, session_id: int) -> list[ChangeSetRead]:
    """Return a known session's change sets, proposals, and hunks in stable order."""
    sets = (
        await db.exec(
            select(ChangeSet)
            .where(ChangeSet.session_id == session_id)
            .order_by(col(ChangeSet.created_at), col(ChangeSet.id))
        )
    ).all()
    changes = (
        await db.exec(
            select(Change)
            .join(ChangeSet, col(Change.change_set_id) == col(ChangeSet.id))
            .where(ChangeSet.session_id == session_id)
            .order_by(col(Change.created_at), col(Change.id))
        )
    ).all()
    hunks = (
        await db.exec(
            select(ChangeHunk)
            .join(Change, col(ChangeHunk.change_id) == col(Change.id))
            .join(ChangeSet, col(Change.change_set_id) == col(ChangeSet.id))
            .where(ChangeSet.session_id == session_id)
            .order_by(col(ChangeHunk.index))
        )
    ).all()
    by_change: dict[int, list[ChangeHunkRead]] = {}
    for hunk in hunks:
        by_change.setdefault(hunk.change_id, []).append(ChangeHunkRead.model_validate(hunk))
    by_set: dict[int, list[ChangeRead]] = {}
    for change in changes:
        assert change.id is not None
        by_set.setdefault(change.change_set_id, []).append(
            ChangeRead.model_validate(
                {**change.model_dump(), "hunks": by_change.get(change.id, [])}
            )
        )
    result: list[ChangeSetRead] = []
    for change_set in sets:
        assert change_set.id is not None
        result.append(
            ChangeSetRead.model_validate(
                {**change_set.model_dump(), "changes": by_set.get(change_set.id, [])}
            )
        )
    return result
