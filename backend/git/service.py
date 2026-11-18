"""Apply approved documentation hunks only in session-owned Git worktrees."""

import asyncio
import logging
import os
import subprocess
from collections.abc import Coroutine, Sequence
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.config import Settings
from backend.errors import DocterError
from backend.git.patches import InvalidPatchError, filter_patch
from backend.git.repository import GitOperationError
from backend.models import Change, ChangeHunk, ChangeSet, Project, Session
from backend.models.change import ChangeStatus
from backend.services.sessions import SessionNotFoundError

logger = logging.getLogger(__name__)


class ChangeNotFoundError(DocterError):
    """The requested persisted proposal no longer exists."""

    status_code = 404
    code = "change_not_found"


class GitService:
    """Own process-local session locks and isolated approval operations."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], worktrees_dir: Path) -> None:
        """Bind database persistence and the configured session worktree directory."""
        self._sessions = sessions
        self._worktrees_dir = worktrees_dir
        self._locks: dict[int, asyncio.Lock] = {}

    async def apply_hunks(self, change: Change, hunk_indices: Sequence[int]) -> Change:
        """Apply and commit selected hunks, returning the persisted review state.

        A failed three-way application records conflict without leaving a dirty
        worktree. Cancellation waits for Git and persistence before releasing the lock.
        """
        if change.id is None:
            raise ChangeNotFoundError("Only persisted changes can be approved.")
        return await _finish_on_cancel(self._apply(change.id, tuple(hunk_indices)))

    async def remove_worktree(self, session: Session) -> None:
        """Remove a clean session worktree while retaining its branch and branch name."""
        if session.id is None:
            raise SessionNotFoundError("Only persisted sessions have worktrees.")
        await _finish_on_cancel(self._remove(session.id))

    async def _apply(self, change_id: int, indices: Sequence[int]) -> Change:
        async with self._sessions() as db:
            change = await self._change(db, change_id)
            change_set = await db.get(ChangeSet, change.change_set_id)
            if change_set is None:
                raise GitOperationError("The change's session no longer exists.")
            session_id = change_set.session_id

        async with self._locks.setdefault(session_id, asyncio.Lock()), self._sessions() as db:
            change = await self._change(db, change_id)
            session, project = await self._context(db, session_id)
            hunks = (
                await db.exec(
                    select(ChangeHunk)
                    .where(ChangeHunk.change_id == change_id)
                    .order_by(col(ChangeHunk.index))
                )
            ).all()
            if change.status == ChangeStatus.APPLIED or any(
                hunk.index in indices and hunk.status == ChangeStatus.APPLIED for hunk in hunks
            ):
                raise InvalidPatchError("The selected change or hunks have already been applied.")
            root = Path(project.path)
            patch = await asyncio.to_thread(
                filter_patch, root, change.file_path, change.patch, indices
            )
            worktree = self._worktrees_dir / str(session_id)
            # Integer session ids are already short and uniquely identify the branch.
            branch = session.branch_name or f"docsmith/{session_id}"
            await asyncio.to_thread(
                _ensure_worktree, root, worktree, branch, session.branch_name is None
            )
            if session.branch_name is None:
                session.branch_name = branch
                await db.commit()
            applied = await asyncio.to_thread(
                _apply_and_commit, worktree, patch, change.file_path, session.mode, change.rationale
            )
            for hunk in hunks:
                if hunk.index in indices:
                    hunk.status = ChangeStatus.APPLIED if applied else ChangeStatus.CONFLICT
            change.status = (
                ChangeStatus.APPLIED
                if applied and all(hunk.status == ChangeStatus.APPLIED for hunk in hunks)
                else ChangeStatus.PARTIAL
                if applied
                else ChangeStatus.CONFLICT
            )
            await db.commit()
            return change

    async def _remove(self, session_id: int) -> None:
        async with self._locks.setdefault(session_id, asyncio.Lock()), self._sessions() as db:
            session, project = await self._context(db, session_id)
            if session.branch_name is not None:
                await asyncio.to_thread(
                    _remove_worktree,
                    Path(project.path),
                    self._worktrees_dir / str(session_id),
                    session.branch_name,
                )

    @staticmethod
    async def _change(db: AsyncSession, change_id: int) -> Change:
        change = await db.get(Change, change_id)
        if change is None:
            raise ChangeNotFoundError(f"Change {change_id} not found")
        return change

    @staticmethod
    async def _context(db: AsyncSession, session_id: int) -> tuple[Session, Project]:
        session = await db.get(Session, session_id)
        if session is None:
            raise SessionNotFoundError(f"Session {session_id} not found")
        project = await db.get(Project, session.project_id)
        if project is None:
            raise GitOperationError("The session's project no longer exists.")
        return session, project


async def _finish_on_cancel[T](operation: Coroutine[None, None, T]) -> T:
    task = asyncio.create_task(operation)
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
    return result


def _git(
    root: Path, *args: str, patch: str | None = None, check: bool = True
) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            [
                "git",
                "-c",
                f"core.hooksPath={os.devnull}",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "user.name=DocSmith",
                "-c",
                "user.email=docsmith@local",
                *args,
            ],
            cwd=root,
            input=patch.encode("utf-8") if patch is not None else None,
            capture_output=True,
            timeout=Settings.DEFAULT_REQUEST_TIMEOUT_S,
            check=False,
            env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
        )
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise GitOperationError(f"Git {args[0]} could not complete: {exc}") from exc
    if check and result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise GitOperationError(f"Git {args[0]} failed: {detail}")
    return result


def _ensure_worktree(root: Path, worktree: Path, branch: str, create_branch: bool) -> None:
    if worktree.is_symlink():
        raise GitOperationError("A session worktree must not be a symlink.")
    root, worktree = root.resolve(), worktree.resolve()
    if worktree.is_relative_to(root):
        raise GitOperationError("Session worktrees must be outside the project's working tree.")
    if create_branch or not worktree.exists():
        try:
            worktree.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise GitOperationError(f"Cannot create the session worktree directory: {exc}") from exc
        if create_branch:
            _git(root, "worktree", "add", "-b", branch, str(worktree), "HEAD")
        else:
            _git(root, "worktree", "add", str(worktree), branch)
    _check_worktree(root, worktree, branch)


def _check_worktree(root: Path, worktree: Path, branch: str) -> None:
    if worktree.is_symlink() or worktree.resolve().is_relative_to(root.resolve()):
        raise GitOperationError("A session worktree must be separate from the user's working tree.")
    actual_root = Path(_git(worktree, "rev-parse", "--show-toplevel").stdout.decode().strip())
    common = _git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout
    expected = _git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout
    actual_branch = _git(worktree, "symbolic-ref", "--short", "HEAD").stdout.decode().strip()
    if actual_root.resolve() != worktree.resolve() or common != expected or actual_branch != branch:
        raise GitOperationError("The session worktree does not match its project and branch.")


def _apply_and_commit(
    worktree: Path, patch: str, file_path: str, mode: str, rationale: str
) -> bool:
    if _git(worktree, "status", "--porcelain", "--untracked-files=all").stdout:
        raise GitOperationError("The session worktree has uncommitted changes; resolve them first.")
    # Recheck paths in the isolated checkout, which may have changed since proposal time.
    filter_patch(worktree, file_path, patch)
    result = _git(
        worktree, "apply", "--index", "--whitespace=nowarn", "-", patch=patch, check=False
    )
    if result.returncode:
        result = _git(
            worktree,
            "apply",
            "--3way",
            "--index",
            "--whitespace=nowarn",
            "-",
            patch=patch,
            check=False,
        )
    if result.returncode:
        if _git(worktree, "status", "--porcelain", "--untracked-files=all").stdout:
            _git(worktree, "restore", "--source=HEAD", "--staged", "--worktree", "--", file_path)
        logger.warning("Approved patch conflicts in session worktree %s", worktree.name)
        return False
    short_rationale = " ".join(rationale.split())
    if len(short_rationale) > Settings.GIT_RATIONALE_LENGTH:
        short_rationale = short_rationale[: Settings.GIT_RATIONALE_LENGTH - 3].rstrip() + "..."
    try:
        _git(
            worktree,
            "commit",
            "--author=DocSmith <docsmith@local>",
            "-m",
            f"docs({mode}): {file_path} - {short_rationale}",
        )
    except GitOperationError:
        _git(worktree, "restore", "--source=HEAD", "--staged", "--worktree", "--", file_path)
        raise
    return True


def _remove_worktree(root: Path, worktree: Path, branch: str) -> None:
    if not worktree.exists():
        return
    _check_worktree(root, worktree, branch)
    _git(root, "worktree", "remove", str(worktree))
