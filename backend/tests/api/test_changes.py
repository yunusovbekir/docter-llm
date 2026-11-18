"""HTTP review decisions and comment-driven revisions against real Git repositories."""

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from difflib import unified_diff
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from git import Repo
from httpx import AsyncClient
from sqlmodel import col, select

from backend.agent.changes import Ok
from backend.git.repository import GitOperationError
from backend.llm import Done, LLMError, TextDelta, ToolCallRequest
from backend.mcp_client import ToolResult
from backend.models import Change, ChangeSet, Comment, Project, Session
from backend.models.project import IndexStatus
from backend.services.changes import DbChangeSink
from backend.tests.api.test_sessions import (
    Pause,
    ScriptedLLM,
    _events,
    _idle,
    _live_client,
)
from backend.tests.api.test_sessions import llm as llm
from backend.tests.git_utils import TEST_ACTOR, GitRepoFactory
from docter_contract import ProposePatchOutput


@dataclass
class ReviewCase:
    repo: Repo
    root: Path
    worktree: Path
    session_id: int
    change_id: int
    before: str
    after: str
    patch: str
    sink: DbChangeSink


def _proposal(before: str, after: str, path: str = "README.md") -> ProposePatchOutput:
    return ProposePatchOutput(
        file_path=path,
        patch="".join(
            unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
            )
        ),
        rationale="Improve documentation",
        is_new_file=False,
    )


@pytest.fixture
async def review(app: FastAPI, client: AsyncClient, make_git_repo: GitRepoFactory) -> ReviewCase:
    repo = make_git_repo()
    assert repo.working_tree_dir is not None
    root = Path(repo.working_tree_dir)
    before = "".join(f"line {number}\n" for number in range(1, 41))
    after = before.replace("line 2\n", "second\nextra line\n").replace("line 35\n", "later\n")
    (root / "README.md").write_text(before)
    repo.index.add(["README.md"])
    repo.index.commit("Long documentation", author=TEST_ACTOR, committer=TEST_ACTOR)
    async with app.state.sessionmaker() as db:
        project = Project(name="example", path=str(root), index_status=IndexStatus.DONE)
        db.add(project)
        await db.flush()
        assert project.id is not None
        session = Session(project_id=project.id, mode="add")
        db.add(session)
        await db.flush()
        assert session.id is not None
        change_set = ChangeSet(session_id=session.id, run_id="original")
        db.add(change_set)
        await db.commit()
        assert change_set.id is not None
    sink = DbChangeSink(app.state.sessionmaker, change_set.id)
    proposal = _proposal(before, after)
    assert isinstance(await sink.add(proposal), Ok)
    async with app.state.sessionmaker() as db:
        change = (await db.exec(select(Change))).one()
        assert change.id is not None
    return ReviewCase(
        repo,
        root,
        app.state.settings.worktrees_dir / str(session.id),
        session.id,
        change.id,
        before,
        after,
        proposal.patch,
        sink,
    )


async def _saved_changes(client: AsyncClient, review: ReviewCase) -> list[dict[str, Any]]:
    response = await client.get(f"/api/sessions/{review.session_id}/changes")
    assert response.status_code == 200
    return [change for change_set in response.json() for change in change_set["changes"]]


@pytest.mark.parametrize("body", [None, {}, {"hunks": [0, 1]}])
async def test_approve_applies_and_persists(
    client: AsyncClient, review: ReviewCase, body: dict[str, Any] | None
) -> None:
    head = review.repo.head.commit.hexsha

    response = await client.post(f"/api/changes/{review.change_id}/approve", json=body)

    assert response.status_code == 200, response.text
    change = response.json()
    assert change["status"] == "applied"
    assert [hunk["status"] for hunk in change["hunks"]] == ["applied", "applied"]
    assert change["comments"] == []
    assert await _saved_changes(client, review) == [change]
    assert (review.worktree / "README.md").read_text() == review.after
    assert (review.root / "README.md").read_text() == review.before
    assert review.repo.head.commit.hexsha == head
    with Repo(review.worktree) as worktree:
        assert worktree.head.commit.parents[0].hexsha == head
        assert not worktree.is_dirty(untracked_files=True)


async def test_partial_approval_and_default_remaining_approval(
    client: AsyncClient, review: ReviewCase
) -> None:
    url = f"/api/changes/{review.change_id}/approve"
    response = await client.post(url, json={"hunks": [0]})

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "partial"
    assert [hunk["status"] for hunk in response.json()["hunks"]] == ["applied", "pending"]
    assert (review.worktree / "README.md").read_text() == review.before.replace(
        "line 2\n", "second\nextra line\n"
    )

    response = await client.post(url, json={})

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "applied"
    assert [hunk["status"] for hunk in response.json()["hunks"]] == ["applied", "applied"]
    assert (review.worktree / "README.md").read_text() == review.after


async def test_deny_does_not_create_a_worktree(client: AsyncClient, review: ReviewCase) -> None:
    response = await client.post(f"/api/changes/{review.change_id}/deny")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "denied"
    assert [hunk["status"] for hunk in response.json()["hunks"]] == ["denied", "denied"]
    assert await _saved_changes(client, review) == [response.json()]
    assert not review.worktree.exists()
    assert (review.root / "README.md").read_text() == review.before


@pytest.mark.parametrize("first,second", [("approve", "deny"), ("deny", "approve")])
async def test_mixed_decisions_preserve_already_reviewed_hunks(
    client: AsyncClient, review: ReviewCase, first: str, second: str
) -> None:
    url = f"/api/changes/{review.change_id}"
    initial = await client.post(f"{url}/{first}", json={"hunks": [0]})
    assert initial.status_code == 200
    assert initial.json()["status"] == "partial"

    response = await client.post(f"{url}/{second}", json={})

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "partial"
    expected = ["applied", "denied"] if first == "approve" else ["denied", "applied"]
    assert [hunk["status"] for hunk in response.json()["hunks"]] == expected
    expected_content = (
        review.before.replace("line 2\n", "second\nextra line\n")
        if first == "approve"
        else review.before.replace("line 35\n", "later\n")
    )
    assert (review.worktree / "README.md").read_text() == expected_content
    for action in ("approve", "deny"):
        rejected = await client.post(f"{url}/{action}", json={})
        assert rejected.status_code == 409
        assert rejected.json()["error"]["code"] == "change_not_reviewable"


@pytest.mark.parametrize("action", ["approve", "deny"])
@pytest.mark.parametrize(
    "body,code",
    [
        ({"hunks": []}, "invalid_patch"),
        ({"hunks": [0, 0]}, "invalid_patch"),
        ({"hunks": [2]}, "invalid_patch"),
        ({"hunks": [-1]}, "validation_error"),
        ({"hunks": [True]}, "validation_error"),
        ({"hunks": ["0"]}, "validation_error"),
        ({"hunks": [0.0]}, "validation_error"),
        ({"hunks": "all"}, "validation_error"),
        ({"unexpected": True}, "validation_error"),
    ],
)
async def test_invalid_selection_leaves_proposal_unchanged(
    client: AsyncClient, review: ReviewCase, action: str, body: dict[str, Any], code: str
) -> None:
    response = await client.post(f"/api/changes/{review.change_id}/{action}", json=body)

    assert response.status_code == (422 if code == "validation_error" else 400)
    assert response.json()["error"]["code"] == code
    change = (await _saved_changes(client, review))[0]
    assert change["status"] == "pending"
    assert [hunk["status"] for hunk in change["hunks"]] == ["pending", "pending"]
    assert not review.worktree.exists()


@pytest.mark.parametrize("first", ["approve", "deny"])
@pytest.mark.parametrize("second", ["approve", "deny", "comment"])
async def test_final_decisions_cannot_be_overwritten(
    client: AsyncClient, review: ReviewCase, first: str, second: str
) -> None:
    url = f"/api/changes/{review.change_id}"
    assert (await client.post(f"{url}/{first}", json={})).status_code == 200

    response = await client.post(
        f"{url}/{second}", json={"text": "Revise"} if second == "comment" else {}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "change_not_reviewable"


async def test_approve_all_respects_prior_decisions_and_session_scope(
    app: FastAPI, client: AsyncClient, review: ReviewCase
) -> None:
    assert (
        await client.post(f"/api/changes/{review.change_id}/deny", json={"hunks": [0]})
    ).status_code == 200
    new = ProposePatchOutput(
        file_path="new.md",
        patch="--- /dev/null\n+++ b/new.md\n@@ -0,0 +1 @@\n+New documentation\n",
        rationale="Add documentation",
        is_new_file=True,
    )
    assert isinstance(await review.sink.add(new), Ok)
    async with app.state.sessionmaker() as db:
        original_session = await db.get(Session, review.session_id)
        assert original_session is not None
        other = Session(project_id=original_session.project_id, mode="fix")
        db.add(other)
        await db.flush()
        assert other.id is not None
        other_set = ChangeSet(session_id=other.id, run_id="other")
        db.add(other_set)
        await db.commit()
        assert other_set.id is not None
    assert isinstance(await DbChangeSink(app.state.sessionmaker, other_set.id).add(new), Ok)
    url = f"/api/sessions/{review.session_id}/changes/approve-all"

    response = await client.post(url)

    assert response.status_code == 200, response.text
    changes = response.json()
    assert [change["status"] for change in changes] == ["partial", "applied"]
    assert [hunk["status"] for hunk in changes[0]["hunks"]] == ["denied", "applied"]
    assert (review.worktree / "new.md").read_text() == "New documentation\n"
    assert (review.worktree / "README.md").read_text() == review.before.replace(
        "line 35\n", "later\n"
    )
    other_changes = (await client.get(f"/api/sessions/{other.id}/changes")).json()
    assert other_changes[0]["changes"][0]["status"] == "pending"
    assert (await client.post(url)).json() == []


async def test_apply_conflict_is_exposed_and_durable(
    client: AsyncClient, review: ReviewCase
) -> None:
    (review.root / "README.md").write_text("Competing content\n")
    review.repo.index.add(["README.md"])
    review.repo.index.commit("Competing", author=TEST_ACTOR, committer=TEST_ACTOR)

    response = await client.post(f"/api/changes/{review.change_id}/approve", json={})

    assert response.status_code == 200
    assert response.json()["status"] == "conflict"
    assert [hunk["status"] for hunk in response.json()["hunks"]] == ["conflict", "conflict"]
    assert await _saved_changes(client, review) == [response.json()]
    with Repo(review.worktree) as worktree:
        assert not worktree.is_dirty(untracked_files=True)


async def test_git_failure_returns_domain_error_without_approving(
    app: FastAPI, client: AsyncClient, review: ReviewCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        app.state.git, "apply_hunks", AsyncMock(side_effect=GitOperationError("Cannot commit"))
    )

    response = await client.post(f"/api/changes/{review.change_id}/approve", json={})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "git_error"
    saved = (await _saved_changes(client, review))[0]
    assert saved["status"] == "pending"
    assert [hunk["status"] for hunk in saved["hunks"]] == ["pending", "pending"]


async def test_concurrent_decisions_do_not_overwrite_each_other(
    client: AsyncClient, review: ReviewCase
) -> None:
    responses = await asyncio.gather(
        client.post(f"/api/changes/{review.change_id}/approve", json={}),
        client.post(f"/api/changes/{review.change_id}/deny", json={}),
    )

    assert sorted(response.status_code for response in responses) == [200, 409]
    accepted = next(response.json() for response in responses if response.status_code == 200)
    assert accepted["status"] in {"applied", "denied"}
    assert [hunk["status"] for hunk in accepted["hunks"]] == [accepted["status"]] * 2
    assert await _saved_changes(client, review) == [accepted]
    assert review.worktree.exists() == (accepted["status"] == "applied")


@pytest.mark.parametrize("action", ["approve", "deny"])
async def test_hunkless_empty_file_proposal(
    app: FastAPI, client: AsyncClient, review: ReviewCase, action: str
) -> None:
    output = ProposePatchOutput(
        file_path="empty.md",
        patch="diff --git a/empty.md b/empty.md\nnew file mode 100644\n",
        rationale="Create empty documentation",
        is_new_file=True,
    )
    assert isinstance(await review.sink.add(output), Ok)
    async with app.state.sessionmaker() as db:
        change = (await db.exec(select(Change).order_by(col(Change.id).desc()))).first()
        assert change is not None

    response = await client.post(f"/api/changes/{change.id}/{action}", json={})

    assert response.status_code == 200, response.text
    assert response.json()["status"] == ("applied" if action == "approve" else "denied")
    assert response.json()["hunks"] == []
    assert (review.worktree / "empty.md").exists() == (action == "approve")
    if action == "approve":
        assert (review.worktree / "empty.md").read_bytes() == b""
    assert not (review.root / "empty.md").exists()


@pytest.mark.parametrize("action", ["approve", "deny", "comment"])
async def test_unknown_change_returns_json_error(client: AsyncClient, action: str) -> None:
    response = await client.post(
        f"/api/changes/999/{action}", json={"text": "Revise"} if action == "comment" else {}
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "change_not_found"


async def test_approve_all_unknown_and_empty_session(
    app: FastAPI, client: AsyncClient, review: ReviewCase
) -> None:
    response = await client.post("/api/sessions/999/changes/approve-all")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"
    assert (await client.post(f"/api/changes/{review.change_id}/deny")).status_code == 200
    response = await client.post(f"/api/sessions/{review.session_id}/changes/approve-all")
    assert response.status_code == 200
    assert response.json() == []
    assert not review.worktree.exists()


@pytest.mark.parametrize("hunk_index", [None, 1])
async def test_comment_streams_and_links_a_revision_chain(
    app: FastAPI,
    client: AsyncClient,
    review: ReviewCase,
    llm: ScriptedLLM,
    monkeypatch: pytest.MonkeyPatch,
    hunk_index: int | None,
) -> None:
    parent_id = review.change_id
    previous_patch = review.patch
    for index in range(2):
        comment = f"  Explain revision {index}\n"
        output = _proposal(review.before, review.after.replace("later\n", f"revised {index}\n"))
        monkeypatch.setattr(
            app.state.mcp,
            "call_tool",
            AsyncMock(return_value=ToolResult(text="", structured=output.model_dump())),
        )
        llm.turns = [
            [ToolCallRequest(f"revision-{index}", "propose_patch", output.model_dump()), Done()],
            [TextDelta("Revised"), Done()],
        ]
        request_index = len(llm.requests)

        streamed = await _events(
            await client.post(
                f"/api/changes/{parent_id}/comment",
                json={"text": comment, "hunk_index": hunk_index},
            )
        )

        assert [event["event"] for event in streamed] == [
            "tool_call",
            "tool_result",
            "change_proposed",
            "token",
            "done",
        ]
        assert streamed[-1]["data"]["reason"] == "completed"
        prompt = llm.requests[request_index][-1]["content"]
        assert isinstance(prompt, str)
        assert "propose_patch again" in prompt
        context = json.loads(prompt.split("\n\nReview context:\n", 1)[1])
        assert context == {
            "change_id": parent_id,
            "file_path": "README.md",
            "original_patch": previous_patch,
            "comment": comment,
            "hunk_index": hunk_index,
        }
        sets = (await client.get(f"/api/sessions/{review.session_id}/changes")).json()
        assert len(sets) == index + 2
        assert sets[-1]["run_id"] == streamed[0]["id"].rsplit(":", 1)[0]
        changes = [change for change_set in sets for change in change_set["changes"]]
        parent, revision = changes[-2:]
        assert parent["id"] == parent_id
        assert parent["patch"] == previous_patch
        assert parent["status"] == "revised"
        assert [hunk["status"] for hunk in parent["hunks"]] == ["pending", "pending"]
        saved = parent["comments"][0]
        assert saved["id"] > 0
        assert saved["change_id"] == parent_id
        assert saved["text"] == comment
        assert saved["hunk_index"] == hunk_index
        assert datetime.fromisoformat(saved["created_at"]).tzinfo is not None
        assert revision["parent_change_id"] == parent_id
        assert revision["status"] == "pending"
        assert revision["patch"] == output.patch
        assert revision["comments"] == []
        assert not review.worktree.exists()
        parent_id, previous_patch = revision["id"], revision["patch"]

    assert (review.root / "README.md").read_text() == review.before
    async with app.state.sessionmaker() as db:
        comments = (await db.exec(select(Comment).order_by(col(Comment.id)))).all()
        assert len(comments) == 2
        assert [saved.text for saved in comments] == [
            "  Explain revision 0\n",
            "  Explain revision 1\n",
        ]
    for action in ("approve", "deny"):
        response = await client.post(f"/api/changes/{review.change_id}/{action}", json={})
        assert response.status_code == 409
    response = await client.post(f"/api/sessions/{review.session_id}/changes/approve-all")
    assert [change["id"] for change in response.json()] == [parent_id]
    assert response.json()[0]["status"] == "applied"
    assert (review.worktree / "README.md").read_text() == review.after.replace(
        "later\n", "revised 1\n"
    )

    # A later ordinary chat must not inherit the revision's parent.
    output = _proposal(review.before, review.after)
    monkeypatch.setattr(
        app.state.mcp,
        "call_tool",
        AsyncMock(return_value=ToolResult(text="", structured=output.model_dump())),
    )
    llm.turns = [
        [ToolCallRequest("ordinary", "propose_patch", output.model_dump()), Done()],
        [Done()],
    ]
    await _events(
        await client.post(f"/api/sessions/{review.session_id}/chat", json={"message": "Next"})
    )
    assert (await _saved_changes(client, review))[-1]["parent_change_id"] is None


@pytest.mark.parametrize(
    "body,code",
    [
        ({}, "validation_error"),
        ({"text": ""}, "validation_error"),
        ({"text": " \n\t"}, "validation_error"),
        ({"text": 1}, "validation_error"),
        ({"text": "Revise", "hunk_index": -1}, "validation_error"),
        ({"text": "Revise", "hunk_index": True}, "validation_error"),
        ({"text": "Revise", "hunk_index": 2}, "invalid_patch"),
        ({"text": "Revise", "unexpected": True}, "validation_error"),
    ],
)
async def test_invalid_comment_has_no_side_effects(
    app: FastAPI, client: AsyncClient, review: ReviewCase, body: dict[str, Any], code: str
) -> None:
    response = await client.post(f"/api/changes/{review.change_id}/comment", json=body)

    assert response.status_code == (422 if code == "validation_error" else 400)
    assert response.json()["error"]["code"] == code
    changes = await _saved_changes(client, review)
    assert len(changes) == 1
    assert changes[0]["status"] == "pending"
    assert changes[0]["comments"] == []
    session = (await client.get(f"/api/sessions/{review.session_id}")).json()
    assert session["run_status"] == "idle"
    assert session["messages"] == []
    async with app.state.sessionmaker() as db:
        assert (await db.exec(select(Comment))).all() == []


async def test_busy_session_rejects_comment_atomically(
    app: FastAPI, client: AsyncClient, review: ReviewCase, llm: ScriptedLLM
) -> None:
    pause = Pause()
    llm.turns = [[pause, Done()]]
    run = await app.state.sessions.start_chat(review.session_id, "Busy")
    try:
        async with asyncio.timeout(10):
            await pause.reached.wait()
        response = await client.post(
            f"/api/changes/{review.change_id}/comment", json={"text": "Revise"}
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "session_running"
        saved = (await _saved_changes(client, review))[0]
        assert saved["status"] == "pending"
        assert saved["comments"] == []
        async with app.state.sessionmaker() as db:
            assert (await db.exec(select(Comment))).all() == []
            assert len((await db.exec(select(ChangeSet))).all()) == 2
    finally:
        await app.state.sessions.stop(run)
        await run.events.aclose()


async def test_revision_failure_keeps_feedback_and_can_be_retried(
    app: FastAPI,
    client: AsyncClient,
    review: ReviewCase,
    llm: ScriptedLLM,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm.turns = [[LLMError("Unavailable")]]

    streamed = await _events(
        await client.post(
            f"/api/changes/{review.change_id}/comment", json={"text": "Please revise"}
        )
    )

    assert [event["event"] for event in streamed] == ["error", "done"]
    assert streamed[0]["data"]["code"] == "llm_error"
    assert streamed[-1]["data"]["reason"] == "error"
    changes = await _saved_changes(client, review)
    assert len(changes) == 1
    assert changes[0]["status"] == "revised"
    assert changes[0]["comments"][0]["text"] == "Please revise"
    assert (await _idle(client, review.session_id))["run_status"] == "idle"

    output = _proposal(review.before, review.after)
    monkeypatch.setattr(
        app.state.mcp,
        "call_tool",
        AsyncMock(return_value=ToolResult(text="", structured=output.model_dump())),
    )
    llm.turns = [[ToolCallRequest("retry", "propose_patch", output.model_dump()), Done()], [Done()]]

    streamed = await _events(
        await client.post(f"/api/changes/{review.change_id}/comment", json={"text": "Try again"})
    )

    assert streamed[-1]["data"]["reason"] == "completed"
    changes = await _saved_changes(client, review)
    assert [comment["text"] for comment in changes[0]["comments"]] == ["Please revise", "Try again"]
    assert changes[1]["parent_change_id"] == review.change_id
    sets = (await client.get(f"/api/sessions/{review.session_id}/changes")).json()
    assert len(sets) == 3
    assert sets[1]["changes"] == []


async def test_revision_disconnect_uses_chat_cleanup(
    app: FastAPI, client: AsyncClient, review: ReviewCase, llm: ScriptedLLM
) -> None:
    pause = Pause()
    llm.turns = [[TextDelta("Revising"), pause, Done()]]

    async with _live_client(app) as live:
        async with live.stream(
            "POST", f"/api/changes/{review.change_id}/comment", json={"text": "Revise"}
        ) as response:
            assert response.status_code == 200
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    assert json.loads(line.removeprefix("data:"))["type"] == "token"
                    break
        await _idle(live, review.session_id)

    assert llm.closed == 1
    assert not pause.release.is_set()
    saved = (await _saved_changes(client, review))[0]
    assert saved["status"] == "revised"
    assert len(saved["comments"]) == 1
    session = (await client.get(f"/api/sessions/{review.session_id}")).json()
    assert [message["role"] for message in session["messages"]] == ["user"]


async def test_comment_openapi_describes_sse(client: AsyncClient) -> None:
    schema = (await client.get("/openapi.json")).json()
    content = schema["paths"]["/api/changes/{change_id}/comment"]["post"]["responses"]["200"][
        "content"
    ]
    assert set(content) == {"text/event-stream"}
