"""Approvals commit only selected hunks in real, session-owned Git worktrees."""

import asyncio
import threading
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, replace
from difflib import unified_diff
from pathlib import Path

import pytest
from fastapi import FastAPI
from git import Repo
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.agent.changes import Ok
from backend.api.deps import GitServiceDep
from backend.db import create_all, create_engine, create_sessionmaker
from backend.git import service as git_service
from backend.git.patches import InvalidPatchError, filter_patch
from backend.git.repository import GitOperationError
from backend.git.service import ChangeNotFoundError, GitService
from backend.models import Change, ChangeHunk, ChangeSet, Project, Session
from backend.models.change import ChangeStatus
from backend.services.changes import DbChangeSink
from backend.tests.git_utils import TEST_ACTOR, GitRepoFactory
from docter_contract import ProposePatchOutput


@dataclass
class Approval:
    """A persisted session, proposal sink, and isolated Git service."""

    repo: Repo
    root: Path
    session: Session
    sessions: async_sessionmaker[AsyncSession]
    sink: DbChangeSink
    service: GitService
    worktree: Path

    async def propose(
        self,
        before: str = "# Demo\n",
        after: str = "# Documentation\n",
        *,
        file_path: str = "README.md",
        patch: str | None = None,
        new: bool = False,
    ) -> Change:
        """Validate and persist a proposal through the production change sink."""
        if patch is None:
            patch = "".join(
                unified_diff(
                    before.splitlines(keepends=True),
                    after.splitlines(keepends=True),
                    fromfile="/dev/null" if new else f"a/{file_path}",
                    tofile=f"b/{file_path}",
                )
            )
        result = await self.sink.add(
            ProposePatchOutput(
                file_path=file_path, patch=patch, rationale="Improve documentation", is_new_file=new
            )
        )
        assert isinstance(result, Ok), result
        async with self.sessions() as db:
            saved = (await db.exec(select(Change).order_by(col(Change.id).desc()))).first()
            assert saved is not None
            return saved

    async def statuses(self, change: Change) -> tuple[ChangeStatus, list[ChangeStatus]]:
        """Read the durable change and hunk status values."""
        async with self.sessions() as db:
            saved = await db.get(Change, change.id)
            assert saved is not None
            hunks = (
                await db.exec(
                    select(ChangeHunk)
                    .where(ChangeHunk.change_id == change.id)
                    .order_by(col(ChangeHunk.index))
                )
            ).all()
            return saved.status, [hunk.status for hunk in hunks]


@pytest.fixture
async def approval(tmp_path: Path, make_git_repo: GitRepoFactory) -> AsyncIterator[Approval]:
    repo = make_git_repo()
    assert repo.working_tree_dir is not None
    root = Path(repo.working_tree_dir)
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    await create_all(engine)
    sessions = create_sessionmaker(engine)
    async with sessions() as db:
        project = Project(name="example", path=str(root))
        db.add(project)
        await db.flush()
        assert project.id is not None
        session = Session(project_id=project.id, mode="add")
        db.add(session)
        await db.flush()
        assert session.id is not None
        change_set = ChangeSet(session_id=session.id, run_id="run")
        db.add(change_set)
        await db.commit()
        assert change_set.id is not None
    worktrees = tmp_path / "worktrees"
    yield Approval(
        repo,
        root,
        session,
        sessions,
        DbChangeSink(sessions, change_set.id),
        GitService(sessions, worktrees),
        worktrees / str(session.id),
    )
    await engine.dispose()


def _commit_file(approval: Approval, content: str, path: str = "README.md") -> None:
    (approval.root / path).write_text(content)
    approval.repo.index.add([path])
    approval.repo.index.commit("Update source", author=TEST_ACTOR, committer=TEST_ACTOR)


async def test_first_approval_creates_branch_applies_and_commits(approval: Approval) -> None:
    change = await approval.propose()
    original_head = approval.repo.head.commit.hexsha
    assert not approval.worktree.exists()
    assert approval.repo.git.branch("--list", "docsmith/*") == ""

    result = await approval.service.apply_hunks(change, [0])

    assert result.status == ChangeStatus.APPLIED
    assert await approval.statuses(change) == (ChangeStatus.APPLIED, [ChangeStatus.APPLIED])
    async with approval.sessions() as db:
        saved = await db.get(Session, approval.session.id)
        assert saved is not None
        assert saved.branch_name == f"docsmith/{saved.id}"
        branch = saved.branch_name
    with Repo(approval.worktree) as worktree:
        assert worktree.active_branch.name == branch
        assert worktree.head.commit.parents[0].hexsha == original_head
        assert worktree.head.commit.message == ("docs(add): README.md - Improve documentation\n")
        assert worktree.head.commit.author.name == "DocSmith"
        assert worktree.head.commit.author.email == "docsmith@local"
        assert worktree.head.commit.committer.email == "docsmith@local"
        assert not worktree.is_dirty(untracked_files=True)
    assert (approval.worktree / "README.md").read_text() == "# Documentation\n"
    assert approval.repo.active_branch.name == "main"
    assert approval.repo.head.commit.hexsha == original_head
    assert (approval.root / "README.md").read_text() == "# Demo\n"


@pytest.mark.parametrize("selected", [[0], [1]])
async def test_partial_hunks_and_later_approval(approval: Approval, selected: list[int]) -> None:
    before = "".join(f"line {number}\n" for number in range(1, 41))
    after = before.replace("line 2\n", "second\nextra line\n").replace("line 35\n", "later\n")
    _commit_file(approval, before)
    change = await approval.propose(before, after)

    await approval.service.apply_hunks(change, selected)

    partial = (
        before.replace("line 2\n", "second\nextra line\n")
        if selected == [0]
        else before.replace("line 35\n", "later\n")
    )
    assert (approval.worktree / "README.md").read_text() == partial
    status, hunks = await approval.statuses(change)
    assert status == ChangeStatus.PARTIAL
    assert hunks[selected[0]] == ChangeStatus.APPLIED
    assert hunks[1 - selected[0]] == ChangeStatus.PENDING

    await approval.service.apply_hunks(change, [1 - selected[0]])

    assert (approval.worktree / "README.md").read_text() == after
    assert await approval.statuses(change) == (
        ChangeStatus.APPLIED,
        [ChangeStatus.APPLIED, ChangeStatus.APPLIED],
    )
    with Repo(approval.worktree) as worktree:
        assert len(list(worktree.iter_commits())) == 4
    assert (approval.root / "README.md").read_text() == before


async def test_conflict_is_persisted_and_does_not_block_next_approval(approval: Approval) -> None:
    conflicting = await approval.propose()
    _commit_file(approval, "# Changed after proposal\n")
    original_head = approval.repo.head.commit.hexsha

    result = await approval.service.apply_hunks(conflicting, [0])

    assert result.status == ChangeStatus.CONFLICT
    assert await approval.statuses(conflicting) == (ChangeStatus.CONFLICT, [ChangeStatus.CONFLICT])
    with Repo(approval.worktree) as worktree:
        assert worktree.head.commit.hexsha == original_head
        assert not worktree.is_dirty(untracked_files=True)
    change = await approval.propose("# Changed after proposal\n", "# Fixed\n")
    assert (await approval.service.apply_hunks(change, [0])).status == ChangeStatus.APPLIED
    assert (approval.worktree / "README.md").read_text() == "# Fixed\n"


async def test_three_way_fallback_merges_nonoverlapping_changes(approval: Approval) -> None:
    before = "first\nsecond\nthird\nfourth\nfifth\n"
    _commit_file(approval, before)
    (approval.root / "README.md").write_text(before.replace("first\n", "documented first\n"))
    patch = approval.repo.git.diff("--full-index", "--unified=5", "HEAD") + "\n"
    (approval.root / "README.md").write_text(before)
    change = await approval.propose(patch=patch)
    _commit_file(approval, before.replace("fifth\n", "updated fifth\n"))

    await approval.service.apply_hunks(change, [0])

    assert await approval.statuses(change) == (ChangeStatus.APPLIED, [ChangeStatus.APPLIED])
    assert (approval.worktree / "README.md").read_text() == (
        "documented first\nsecond\nthird\nfourth\nupdated fifth\n"
    )


async def test_three_way_conflict_cleans_index_and_conflict_markers(approval: Approval) -> None:
    (approval.root / "README.md").write_text("# Proposed\n")
    patch = approval.repo.git.diff("--full-index", "HEAD") + "\n"
    (approval.root / "README.md").write_text("# Demo\n")
    change = await approval.propose(patch=patch)
    _commit_file(approval, "# Competing\n")

    await approval.service.apply_hunks(change, [0])

    assert await approval.statuses(change) == (ChangeStatus.CONFLICT, [ChangeStatus.CONFLICT])
    assert (approval.worktree / "README.md").read_text() == "# Competing\n"
    with Repo(approval.worktree) as worktree:
        assert not worktree.index.unmerged_blobs()
        assert not worktree.is_dirty(untracked_files=True)


async def test_dirty_user_worktree_index_branch_and_environment_are_untouched(
    approval: Approval, monkeypatch: pytest.MonkeyPatch
) -> None:
    change = await approval.propose()
    staged = approval.root / "staged.md"
    staged.write_text("staged\n")
    approval.repo.index.add(["staged.md"])
    staged.write_text("unstaged over staged\n")
    (approval.root / "README.md").write_text("# User's uncommitted edits\n")
    (approval.root / "untracked.md").write_text("untracked\n")
    index = (approval.root / ".git" / "index").read_bytes()
    head = (approval.root / ".git" / "HEAD").read_bytes()
    branch_head = approval.repo.head.commit.hexsha
    remote_path = approval.root.parent / "remote.git"
    approval.repo.create_remote("origin", str(remote_path))
    config = (approval.root / ".git" / "config").read_bytes()
    monkeypatch.setenv("GIT_DIR", str(approval.root / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(approval.root))
    monkeypatch.setenv("GIT_INDEX_FILE", str(approval.root / ".git" / "index"))
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Not DocSmith")

    await approval.service.apply_hunks(change, [0])

    assert (approval.root / ".git" / "index").read_bytes() == index
    assert (approval.root / ".git" / "HEAD").read_bytes() == head
    assert (approval.root / ".git" / "config").read_bytes() == config
    assert (approval.root / "README.md").read_text() == "# User's uncommitted edits\n"
    assert staged.read_text() == "unstaged over staged\n"
    assert (approval.root / "untracked.md").read_text() == "untracked\n"
    assert approval.repo.head.commit.hexsha == branch_head
    assert not remote_path.exists()


@pytest.mark.parametrize("indices", [[], [-1], [1], [0, 0]])
async def test_invalid_selection_creates_no_worktree(
    approval: Approval, indices: Sequence[int]
) -> None:
    change = await approval.propose()
    with pytest.raises(InvalidPatchError):
        await approval.service.apply_hunks(change, indices)
    assert not approval.worktree.exists()
    assert await approval.statuses(change) == (ChangeStatus.PENDING, [ChangeStatus.PENDING])


async def test_already_applied_hunk_is_rejected(approval: Approval) -> None:
    change = await approval.propose()
    await approval.service.apply_hunks(change, [0])

    with pytest.raises(InvalidPatchError, match="already"):
        await approval.service.apply_hunks(change, [0])

    assert await approval.statuses(change) == (ChangeStatus.APPLIED, [ChangeStatus.APPLIED])


async def test_remove_keeps_branch_and_recreation_resumes_it(approval: Approval) -> None:
    change = await approval.propose()
    await approval.service.apply_hunks(change, [0])
    with Repo(approval.worktree) as worktree:
        commit = worktree.head.commit.hexsha

    await approval.service.remove_worktree(approval.session)
    await approval.service.remove_worktree(approval.session)

    assert not approval.worktree.exists()
    async with approval.sessions() as db:
        saved = await db.get(Session, approval.session.id)
        assert saved is not None
        assert saved.branch_name is not None
        assert approval.repo.commit(saved.branch_name).hexsha == commit
    _commit_file(approval, "# Documentation\n")
    later = await approval.propose("# Documentation\n", "# More documentation\n")
    await approval.service.apply_hunks(later, [0])
    with Repo(approval.worktree) as worktree:
        assert worktree.head.commit.parents[0].hexsha == commit


async def test_dirty_session_worktree_is_not_overwritten_or_removed(approval: Approval) -> None:
    change = await approval.propose()
    await approval.service.apply_hunks(change, [0])
    dirty = approval.worktree / "user-note.md"
    dirty.write_text("keep me\n")
    other = await approval.propose(after="# Other docs\n")

    with pytest.raises(GitOperationError, match="uncommitted"):
        await approval.service.apply_hunks(other, [0])
    with pytest.raises(GitOperationError):
        await approval.service.remove_worktree(approval.session)

    assert dirty.read_text() == "keep me\n"


async def test_worktree_directory_must_be_outside_project(approval: Approval) -> None:
    change = await approval.propose()
    service = GitService(approval.sessions, approval.root / "worktrees")

    with pytest.raises(GitOperationError, match="outside"):
        await service.apply_hunks(change, [0])

    assert not (approval.root / "worktrees").exists()


@pytest.mark.parametrize(
    ("patch", "new", "indices", "expected"),
    [
        ("--- /dev/null\n+++ b/docs.md\n@@ -0,0 +1 @@\n+New docs\n", True, [0], b"New docs\n"),
        ("diff --git a/docs.md b/docs.md\nnew file mode 100644\n", True, [], b""),
        ("--- a/README.md\n+++ /dev/null\n@@ -1 +0,0 @@\n-# Demo\n", False, [0], None),
        (
            "--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-# Demo\n"
            "+# Documentation\n\\ No newline at end of file\n",
            False,
            [0],
            b"# Documentation",
        ),
    ],
)
async def test_creation_empty_creation_deletion_and_no_final_newline(
    approval: Approval, patch: str, new: bool, indices: list[int], expected: bytes | None
) -> None:
    path = "docs.md" if new else "README.md"
    change = await approval.propose(patch=patch, file_path=path, new=new)

    assert (await approval.service.apply_hunks(change, indices)).status == ChangeStatus.APPLIED

    target = approval.worktree / path
    assert target.exists() is (expected is not None)
    if expected is not None:
        assert target.read_bytes() == expected


async def test_concurrent_duplicate_approvals_are_serialized(approval: Approval) -> None:
    change = await approval.propose()

    results = await asyncio.gather(
        approval.service.apply_hunks(change, [0]),
        approval.service.apply_hunks(change, [0]),
        return_exceptions=True,
    )

    assert sum(isinstance(result, Change) for result in results) == 1
    assert sum(isinstance(result, InvalidPatchError) for result in results) == 1
    with Repo(approval.worktree) as worktree:
        assert len(list(worktree.iter_commits())) == 2


async def test_cancellation_waits_for_git_and_persistence(
    approval: Approval, monkeypatch: pytest.MonkeyPatch
) -> None:
    change = await approval.propose()
    entered, release = threading.Event(), threading.Event()
    original = git_service._apply_and_commit
    loop_thread = threading.get_ident()

    def blocked(worktree: Path, patch: str, file_path: str, mode: str, rationale: str) -> bool:
        assert threading.get_ident() != loop_thread
        entered.set()
        assert release.wait(timeout=10)
        return original(worktree, patch, file_path, mode, rationale)

    monkeypatch.setattr(git_service, "_apply_and_commit", blocked)
    task = asyncio.create_task(approval.service.apply_hunks(change, [0]))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)

    assert await approval.statuses(change) == (ChangeStatus.APPLIED, [ChangeStatus.APPLIED])


async def test_different_sessions_can_apply_concurrently(
    approval: Approval, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = await approval.propose()
    async with approval.sessions() as db:
        session = Session(project_id=approval.session.project_id, mode="fix")
        db.add(session)
        await db.flush()
        assert session.id is not None
        change_set = ChangeSet(session_id=session.id, run_id="second-run")
        db.add(change_set)
        await db.commit()
        assert change_set.id is not None
    second_session = replace(
        approval,
        session=session,
        sink=DbChangeSink(approval.sessions, change_set.id),
        worktree=approval.worktree.parent / str(session.id),
    )
    second = await second_session.propose()
    entered, release = threading.Event(), threading.Event()
    original = git_service._apply_and_commit

    def blocked(worktree: Path, patch: str, file_path: str, mode: str, rationale: str) -> bool:
        if worktree == approval.worktree:
            entered.set()
            assert release.wait(timeout=10)
        return original(worktree, patch, file_path, mode, rationale)

    monkeypatch.setattr(git_service, "_apply_and_commit", blocked)
    first_task = asyncio.create_task(approval.service.apply_hunks(first, [0]))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        result = await asyncio.wait_for(approval.service.apply_hunks(second, [0]), timeout=5)
        assert result.status == ChangeStatus.APPLIED
        assert not first_task.done()
    finally:
        release.set()
        await first_task
    assert (second_session.worktree / "README.md").read_text() == "# Documentation\n"
    with Repo(second_session.worktree) as worktree:
        message = worktree.head.commit.message
        assert isinstance(message, str)
        assert message.startswith("docs(fix):")


async def test_selected_hunks_only_are_used_by_three_way_fallback(approval: Approval) -> None:
    before = "".join(f"line {number}\n" for number in range(1, 41))
    after = before.replace("line 2\n", "second\nextra line\n").replace("line 35\n", "later\n")
    _commit_file(approval, before)
    (approval.root / "README.md").write_text(after)
    patch = approval.repo.git.diff("--full-index", "HEAD") + "\n"
    (approval.root / "README.md").write_text(before)
    change = await approval.propose(patch=patch)
    _commit_file(approval, before.replace("line 32\n", "updated context\n"))

    await approval.service.apply_hunks(change, [1])

    assert (approval.worktree / "README.md").read_text() == (
        before.replace("line 32\n", "updated context\n").replace("line 35\n", "later\n")
    )
    assert await approval.statuses(change) == (
        ChangeStatus.PARTIAL,
        [ChangeStatus.PENDING, ChangeStatus.APPLIED],
    )


async def test_repository_hooks_and_signing_do_not_run(approval: Approval) -> None:
    change = await approval.propose()
    hook = approval.root / ".git" / "hooks" / "post-checkout"
    hook.write_text(f"#!/bin/sh\nprintf overwritten > '{approval.root / 'README.md'}'\n")
    hook.chmod(0o755)
    with approval.repo.config_writer() as config:
        config.set_value("commit", "gpgsign", "true")
        config.set_value("gpg", "program", "nonexistent-signing-program")
    config_before = (approval.root / ".git" / "config").read_bytes()

    await approval.service.apply_hunks(change, [0])

    assert (approval.root / "README.md").read_text() == "# Demo\n"
    assert (approval.root / ".git" / "config").read_bytes() == config_before
    assert await approval.statuses(change) == (ChangeStatus.APPLIED, [ChangeStatus.APPLIED])


async def test_detached_head_and_short_single_line_rationale(approval: Approval) -> None:
    approval.repo.git.checkout("--detach")
    change = await approval.propose()
    async with approval.sessions() as db:
        saved = await db.get(Change, change.id)
        assert saved is not None
        saved.rationale = "Improve\n  documentation " + "x" * 100
        await db.commit()

    await approval.service.apply_hunks(change, [0])

    assert approval.repo.head.is_detached
    with Repo(approval.worktree) as worktree:
        message = worktree.head.commit.message
        assert isinstance(message, str)
        assert message.startswith("docs(add): README.md - Improve documentation ")
        assert len(message.splitlines()) == 1
        assert message.endswith("...\n")


async def test_crlf_patch_preserves_line_endings(approval: Approval) -> None:
    (approval.root / "README.md").write_bytes(b"# Demo\r\n")
    approval.repo.index.add(["README.md"])
    approval.repo.index.commit("CRLF source", author=TEST_ACTOR, committer=TEST_ACTOR)
    change = await approval.propose("# Demo\r\n", "# Documentation\r\n")

    await approval.service.apply_hunks(change, [0])

    assert (approval.worktree / "README.md").read_bytes() == b"# Documentation\r\n"


async def test_unknown_change_is_a_domain_error(approval: Approval) -> None:
    change = await approval.propose()
    change.id = 99999
    with pytest.raises(ChangeNotFoundError):
        await approval.service.apply_hunks(change, [0])


async def test_git_service_is_app_scoped_and_injectable(app: FastAPI, client: AsyncClient) -> None:
    @app.get("/test/git-service")
    async def service_identity(service: GitServiceDep) -> bool:
        return service is app.state.git

    assert (await client.get("/test/git-service")).json() is True


def test_filtered_partial_deletion_does_not_delete_file(tmp_path: Path) -> None:
    patch = (
        "diff --git a/docs.md b/docs.md\ndeleted file mode 100644\n"
        "--- a/docs.md\n+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n-one\n-two\n"
        "@@ -3,2 +0,0 @@\n-three\n-four\n"
    )

    filtered = filter_patch(tmp_path, "docs.md", patch, [1])

    assert "deleted file mode" not in filtered
    assert "+++ b/docs.md\n" in filtered
    assert "-one\n" not in filtered
    assert "-three\n" in filtered
