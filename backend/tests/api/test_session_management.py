"""Project session summaries and deletion with real Git worktrees."""

import asyncio
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, Response
from sqlmodel import select

from backend.llm import Done
from backend.models import Change, ChangeHunk, ChangeSet, Comment, Message, Project, Session
from backend.models.change import ChangeStatus
from backend.models.types import utcnow
from backend.tests.api.test_sessions import Pause, ScriptedLLM, _project, _session
from backend.tests.api.test_sessions import llm as llm
from backend.tests.git_utils import GitRepoFactory


async def _proposals(
    app: FastAPI, session_id: int, statuses: Sequence[ChangeStatus]
) -> list[Change]:
    async with app.state.sessionmaker() as db:
        change_set = ChangeSet(session_id=session_id, run_id=uuid4().hex)
        db.add(change_set)
        await db.flush()
        assert change_set.id is not None
        changes = [
            Change(
                change_set_id=change_set.id,
                file_path="README.md",
                patch="--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-# Demo\n+# Docs\n",
                rationale="Improve heading",
                status=status,
                original_content="# Demo\n",
                proposed_content="# Docs\n",
            )
            for status in statuses
        ]
        db.add_all(changes)
        await db.flush()
        for change in changes:
            assert change.id is not None
            db.add(ChangeHunk(change_id=change.id, index=0, header="@@ -1 +1 @@"))
            db.add(Comment(change_id=change.id, text="Review feedback"))
        db.add(Message(session_id=session_id, role="user", content="Improve documentation"))
        await db.commit()
        return changes


async def test_list_sessions_with_counts_across_runs(app: FastAPI, client: AsyncClient) -> None:
    project_id = await _project(app)
    first = await _session(client, project_id)
    second = await _session(client, project_id)
    other = await _session(client, await _project(app, path="/other"))
    await _proposals(app, first, list(ChangeStatus))
    await _proposals(app, first, [ChangeStatus.PENDING, ChangeStatus.APPLIED])
    await _proposals(app, other, [ChangeStatus.DENIED])
    async with app.state.sessionmaker() as db:
        timestamp = utcnow()
        for session_id in (first, second):
            row = await db.get(Session, session_id)
            assert row is not None
            row.created_at = timestamp
        await db.commit()

    response = await client.get("/api/sessions", params={"project_id": project_id})

    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["id"] for row in body] == [first, second]
    assert body[0]["change_counts"] == {"pending": 2, "applied": 2, "denied": 1}
    assert body[1]["change_counts"] == {"pending": 0, "applied": 0, "denied": 0}
    detail = (await client.get(f"/api/sessions/{first}")).json()
    assert body[0] == {key: value for key, value in detail.items() if key != "messages"} | {
        "change_counts": {"pending": 2, "applied": 2, "denied": 1}
    }

    response = await client.get("/api/sessions")
    assert response.status_code == 200, response.text
    by_id = {row["id"]: row for row in response.json()}
    assert set(by_id) == {first, second, other}
    assert by_id[first] == body[0]
    assert by_id[second] == body[1]
    assert by_id[other]["change_counts"] == {"pending": 0, "applied": 0, "denied": 1}


async def test_empty_and_unknown_project_lists(app: FastAPI, client: AsyncClient) -> None:
    assert (await client.get("/api/sessions")).json() == []
    response = await client.get("/api/sessions", params={"project_id": await _project(app)})
    assert response.status_code == 200
    assert response.json() == []
    response = await client.get("/api/sessions", params={"project_id": 999})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "project_not_found"


@pytest.mark.parametrize("query", ["?project_id=0", "?project_id=-1", "?project_id=text"])
async def test_list_validates_project_id_when_provided(client: AsyncClient, query: str) -> None:
    response = await client.get(f"/api/sessions{query}")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_delete_unknown_session(client: AsyncClient) -> None:
    response = await client.delete("/api/sessions/999")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"


@pytest.mark.parametrize("worktree_state", ["never_created", "present", "already_removed"])
async def test_delete_history_and_worktree_keeps_branch(
    app: FastAPI,
    client: AsyncClient,
    make_git_repo: GitRepoFactory,
    worktree_state: str,
) -> None:
    async with app.state.engine.begin() as connection:
        await connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    repo = make_git_repo()
    assert repo.working_tree_dir is not None
    root = Path(repo.working_tree_dir)
    project_id = await _project(app, path=str(root))
    other = await _session(client, project_id)
    session_id = await _session(client, project_id)
    changes = await _proposals(app, session_id, [ChangeStatus.PENDING])
    # Include a revision's self-reference in the history being removed.
    revisions = await _proposals(app, session_id, [ChangeStatus.PENDING])
    async with app.state.sessionmaker() as db:
        revision = await db.get(Change, revisions[0].id)
        assert revision is not None
        revision.parent_change_id = changes[0].id
        await db.commit()
    await _proposals(app, other, [ChangeStatus.DENIED])
    worktree = app.state.settings.worktrees_dir / str(session_id)
    branch: str | None = None
    commit: str | None = None
    if worktree_state != "never_created":
        applied = await app.state.git.apply_hunks(changes[0], [0])
        assert applied.status == ChangeStatus.APPLIED
        row = (await client.get(f"/api/sessions/{session_id}")).json()
        branch = row["branch_name"]
        commit = repo.commit(branch).hexsha
        if worktree_state == "already_removed":
            async with app.state.sessionmaker() as db:
                saved = await db.get(Session, session_id)
                assert saved is not None
            await app.state.git.remove_worktree(saved)
    original_head = repo.head.commit.hexsha

    response = await client.delete(f"/api/sessions/{session_id}")

    assert response.status_code == 204, response.text
    assert response.content == b""
    assert not worktree.exists()
    if branch is not None:
        assert repo.commit(branch).hexsha == commit
    assert repo.head.commit.hexsha == original_head
    assert (root / "README.md").read_text() == "# Demo\n"
    assert (await client.get(f"/api/sessions/{session_id}")).status_code == 404
    assert (await client.get(f"/api/sessions/{other}")).status_code == 200
    async with app.state.sessionmaker() as db:
        assert await db.get(Project, project_id) is not None
        assert [row.id for row in (await db.exec(select(Session))).all()] == [other]
        assert [row.session_id for row in (await db.exec(select(ChangeSet))).all()] == [other]
        assert [row.session_id for row in (await db.exec(select(Message))).all()] == [other]
        for model in (Change, ChangeHunk, Comment):
            assert len((await db.exec(select(model))).all()) == 1
    listed = (await client.get("/api/sessions", params={"project_id": project_id})).json()
    assert [row["id"] for row in listed] == [other]
    assert (await client.delete(f"/api/sessions/{session_id}")).status_code == 404


async def test_dirty_worktree_prevents_deleting_history(
    app: FastAPI, client: AsyncClient, make_git_repo: GitRepoFactory
) -> None:
    repo = make_git_repo()
    assert repo.working_tree_dir is not None
    session_id = await _session(client, await _project(app, path=str(repo.working_tree_dir)))
    changes = await _proposals(app, session_id, [ChangeStatus.PENDING])
    await app.state.git.apply_hunks(changes[0], [0])
    worktree = app.state.settings.worktrees_dir / str(session_id)
    edited = worktree / "README.md"
    edited.write_text("# Uncommitted user edits\n")

    response = await client.delete(f"/api/sessions/{session_id}")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "git_error"
    assert edited.read_text() == "# Uncommitted user edits\n"
    assert (await client.get(f"/api/sessions/{session_id}")).status_code == 200
    assert len((await client.get(f"/api/sessions/{session_id}/changes")).json()) == 1


async def test_new_session_approval_preserves_deleted_sessions_branch(
    app: FastAPI, client: AsyncClient, make_git_repo: GitRepoFactory
) -> None:
    repo = make_git_repo()
    assert repo.working_tree_dir is not None
    project_id = await _project(app, path=str(repo.working_tree_dir))
    old_id = await _session(client, project_id)
    old_change = (await _proposals(app, old_id, [ChangeStatus.PENDING]))[0]
    await app.state.git.apply_hunks(old_change, [0])
    old_branch = (await client.get(f"/api/sessions/{old_id}")).json()["branch_name"]
    old_commit = repo.commit(old_branch).hexsha
    assert (await client.delete(f"/api/sessions/{old_id}")).status_code == 204
    new_id = await _session(client, project_id)
    # SQLite reuses the highest deleted row id in existing databases.
    assert new_id == old_id
    new_change = (await _proposals(app, new_id, [ChangeStatus.PENDING]))[0]

    response = await client.post(f"/api/changes/{new_change.id}/approve")

    assert response.status_code == 200, response.text
    new_branch = (await client.get(f"/api/sessions/{new_id}")).json()["branch_name"]
    assert new_branch != old_branch
    assert new_branch.startswith(f"docsmith/{new_id}-")
    assert repo.commit(old_branch).hexsha == old_commit
    assert response.json()["status"] == "applied"


async def test_delete_stops_active_chat_before_deleting_history(
    app: FastAPI, client: AsyncClient, llm: ScriptedLLM
) -> None:
    session_id = await _session(client, await _project(app))
    pause = Pause()
    llm.turns = [[pause, Done()]]
    run = await app.state.sessions.start_chat(session_id, "Start")
    try:
        async with asyncio.timeout(10):
            await pause.reached.wait()
            response = await client.delete(f"/api/sessions/{session_id}")
        assert response.status_code == 204, response.text
        assert run.task.done()
        assert llm.closed == 1
        assert (await client.get(f"/api/sessions/{session_id}")).status_code == 404
        async with app.state.sessionmaker() as db:
            assert (await db.exec(select(Message))).all() == []
            assert (await db.exec(select(ChangeSet))).all() == []
    finally:
        await app.state.sessions.stop(run)
        await run.events.aclose()


async def test_delete_serializes_approval_and_new_chat(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = await _session(client, await _project(app))
    changes = await _proposals(app, session_id, [ChangeStatus.PENDING])
    pause = Pause()

    async def remove_worktree(session: Session) -> None:
        await pause.wait()

    monkeypatch.setattr(app.state.git, "remove_worktree", remove_worktree)
    deletion = asyncio.create_task(client.delete(f"/api/sessions/{session_id}"))
    approval: asyncio.Task[Response] | None = None
    chat: asyncio.Task[Response] | None = None
    try:
        async with asyncio.timeout(10):
            await pause.reached.wait()
            approval = asyncio.create_task(client.post(f"/api/changes/{changes[0].id}/approve"))
            chat = asyncio.create_task(
                client.post(f"/api/sessions/{session_id}/chat", json={"message": "Start"})
            )
            await asyncio.sleep(0)
            assert not approval.done()
            assert not chat.done()
            pause.release.set()
            responses = await asyncio.gather(deletion, approval, chat)
        assert [response.status_code for response in responses] == [204, 404, 404]
    finally:
        pause.release.set()
        await asyncio.gather(*(task for task in (deletion, approval, chat) if task is not None))
