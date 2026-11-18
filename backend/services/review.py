"""Serialized review decisions and comment-driven agent revisions."""

import asyncio
import json
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.errors import DocterError
from backend.git.patches import InvalidPatchError
from backend.git.service import GitService
from backend.models import Change, ChangeHunk, ChangeSet, Comment, Session
from backend.models.change import ChangeStatus
from backend.models.changes import ChangeRead, CommentCreate
from backend.services.changes import get_change, get_change_read, get_hunks
from backend.services.sessions import ChatRun, SessionNotFoundError, SessionService


class ChangeNotReviewableError(DocterError):
    """A decision would overwrite an applied, denied, or superseded proposal."""

    status_code = 409
    code = "change_not_reviewable"


class ReviewService:
    """Serialize review operations per session while Git owns application and commits."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        git: GitService,
        chat: SessionService,
    ) -> None:
        """Bind app-wide persistence, Git operations, and the chat worker registry."""
        self._sessions = sessions
        self._git = git
        self._chat = chat
        self._locks: dict[int, asyncio.Lock] = {}

    async def delete_session(self, session_id: int) -> None:
        """Delete a session without racing an approval, denial, or revision."""
        async with self._locks.setdefault(session_id, asyncio.Lock()):
            await self._chat.delete(session_id, self._git)

    async def approve(self, change_id: int, hunks: Sequence[int] | None = None) -> ChangeRead:
        """Apply selected reviewable hunks and return their persisted Git result."""
        session_id = await self._session_id(change_id)
        async with self._locks.setdefault(session_id, asyncio.Lock()):
            async with self._sessions() as db:
                change = await get_change(db, change_id)
                selected = _select_hunks(change, await get_hunks(db, change_id), hunks)
            await self._git.apply_hunks(change, selected)
            async with self._sessions() as db:
                return await get_change_read(db, change_id)

    async def deny(self, change_id: int, hunks: Sequence[int] | None = None) -> ChangeRead:
        """Deny remaining or selected hunks without altering already-applied hunks."""
        session_id = await self._session_id(change_id)
        async with self._locks.setdefault(session_id, asyncio.Lock()), self._sessions() as db:
            change = await get_change(db, change_id)
            rows = await get_hunks(db, change_id)
            selected = _select_hunks(change, rows, hunks)
            for hunk in rows:
                if hunk.index in selected:
                    hunk.status = ChangeStatus.DENIED
            change.status = (
                ChangeStatus.DENIED
                if all(hunk.status == ChangeStatus.DENIED for hunk in rows)
                else ChangeStatus.PARTIAL
            )
            await db.commit()
            return await get_change_read(db, change_id)

    async def approve_all(self, session_id: int) -> list[ChangeRead]:
        """Apply remaining reviewable proposals in order, preserving prior decisions."""
        async with self._locks.setdefault(session_id, asyncio.Lock()):
            async with self._sessions() as db:
                if await db.get(Session, session_id) is None:
                    raise SessionNotFoundError(f"Session {session_id} not found")
                changes = (
                    await db.exec(
                        select(Change)
                        .join(ChangeSet, col(Change.change_set_id) == col(ChangeSet.id))
                        .where(ChangeSet.session_id == session_id)
                        .order_by(col(Change.created_at), col(Change.id))
                    )
                ).all()
                pending: list[tuple[Change, list[int]]] = []
                for change in changes:
                    if not _reviewable(change.status):
                        continue
                    assert change.id is not None
                    hunks = await get_hunks(db, change.id)
                    remaining = [hunk.index for hunk in hunks if _reviewable(hunk.status)]
                    if remaining or not hunks:
                        pending.append((change, remaining))
            result = []
            for change, selected in pending:
                await self._git.apply_hunks(change, selected)
                assert change.id is not None
                async with self._sessions() as db:
                    result.append(await get_change_read(db, change.id))
            return result

    async def comment(self, change_id: int, data: CommentCreate) -> ChatRun:
        """Save feedback and supersede the proposal atomically with run reservation."""
        session_id = await self._session_id(change_id)
        async with self._locks.setdefault(session_id, asyncio.Lock()):
            async with self._sessions() as db:
                change = await get_change(db, change_id)
                if change.status != ChangeStatus.REVISED:
                    _ensure_reviewable(change)
                hunks = await get_hunks(db, change_id)
                if data.hunk_index is not None and not any(
                    hunk.index == data.hunk_index for hunk in hunks
                ):
                    raise InvalidPatchError("Select a valid zero-based hunk index.")
                message = (
                    "Revise this documentation proposal using the review comment below. "
                    "Use the original patch as revision context. "
                    "Call propose_patch again with the revised patch; do not write or apply "
                    "changes directly to the repository.\n\nReview context:\n"
                    + json.dumps(
                        {
                            "change_id": change_id,
                            "file_path": change.file_path,
                            "original_patch": change.patch,
                            "comment": data.text,
                            "hunk_index": data.hunk_index,
                        }
                    )
                )
            return await self._chat.start_revision(
                session_id,
                message,
                Comment(change_id=change_id, hunk_index=data.hunk_index, text=data.text),
            )

    async def _session_id(self, change_id: int) -> int:
        async with self._sessions() as db:
            change = await get_change(db, change_id)
            change_set = await db.get(ChangeSet, change.change_set_id)
            if change_set is None:
                raise SessionNotFoundError("The change's session no longer exists.")
            return change_set.session_id


def _reviewable(status: ChangeStatus) -> bool:
    return status in {
        ChangeStatus.PENDING,
        ChangeStatus.APPROVED,
        ChangeStatus.PARTIAL,
        ChangeStatus.CONFLICT,
    }


def _ensure_reviewable(change: Change) -> None:
    if not _reviewable(change.status):
        raise ChangeNotReviewableError(
            f"Change {change.id} is {change.status} and cannot be reviewed again."
        )


def _select_hunks(
    change: Change, hunks: Sequence[ChangeHunk], indices: Sequence[int] | None
) -> list[int]:
    _ensure_reviewable(change)
    if indices is None:
        selected = [hunk.index for hunk in hunks if _reviewable(hunk.status)]
        if hunks and not selected:
            raise ChangeNotReviewableError("The change has no hunks left to review.")
        return selected
    if (
        len(set(indices)) != len(indices)
        or not set(indices).issubset(hunk.index for hunk in hunks)
        or (hunks and not indices)
    ):
        raise InvalidPatchError("Select distinct, valid zero-based hunk indices.")
    if any(hunk.index in indices and not _reviewable(hunk.status) for hunk in hunks):
        raise ChangeNotReviewableError("Selected hunks have already been applied or denied.")
    return list(indices)
