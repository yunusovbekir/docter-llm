"""Offline HTTP-to-MCP-to-Git approval flow; only the LLM is replaced in-process."""

import shlex
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from git import Repo
from httpx import AsyncClient

from backend.config import Settings
from backend.llm import Done, TextDelta, ToolCallRequest
from backend.services.settings import SettingsService
from backend.tests.api.test_indexing import _wait_for
from backend.tests.api.test_sessions import ScriptedLLM, _events, _idle
from backend.tests.git_utils import GitRepoFactory


@pytest.fixture
def settings(settings: Settings, stub_mcp_command: list[str]) -> Settings:
    return settings.model_copy(update={"mcp_command": shlex.join(stub_mcp_command)})


async def test_project_to_approved_commit(
    app: FastAPI,
    client: AsyncClient,
    make_git_repo: GitRepoFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_git_repo()
    assert repo.working_tree_dir is not None
    root = Path(repo.working_tree_dir)
    original_head = repo.head.commit.hexsha
    original_branch = repo.active_branch.name
    before = (root / "README.md").read_text(encoding="utf-8")
    after = "# Demo\n\nDocumented by Docter.\n"
    patch = "--- a/README.md\n+++ b/README.md\n@@ -1 +1,3 @@\n # Demo\n+\n+Documented by Docter.\n"
    llm = ScriptedLLM()
    llm.turns = [
        [
            ToolCallRequest(
                "propose-docs",
                "propose_patch",
                {"file_path": "README.md", "patch": patch, "rationale": "Document the project"},
            ),
            Done(),
        ],
        [TextDelta("Ready for review."), Done()],
    ]
    monkeypatch.setattr(SettingsService, "gateway", AsyncMock(return_value=llm))

    created = await client.post("/api/projects", json={"path": str(root)})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    assert created.json()["index_status"] == "idle"
    indexing = await client.post(f"/api/projects/{project_id}/index")
    assert indexing.status_code == 202, indexing.text
    assert (await _wait_for(client, project_id, status="done"))["indexed_at"] is not None
    assert app.state.mcp.is_running

    session = await client.post("/api/sessions", json={"project_id": project_id, "mode": "add"})
    assert session.status_code == 201, session.text
    session_id = session.json()["id"]
    response = await client.post(
        f"/api/sessions/{session_id}/chat",
        json={"message": "Document the project"},
        headers={"X-Request-ID": "e2e-chat"},
    )
    assert response.headers["X-Request-ID"] == "e2e-chat"
    streamed = await _events(response)
    assert [event["event"] for event in streamed] == [
        "tool_call",
        "tool_result",
        "change_proposed",
        "token",
        "done",
    ]
    assert streamed[1]["data"]["is_error"] is False
    assert streamed[-1]["data"]["reason"] == "completed"
    assert len(llm.requests) == 2
    detail = await _idle(client, session_id)
    assert [message["role"] for message in detail["messages"]] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert detail["branch_name"] is None
    assert repo.head.commit.hexsha == original_head
    assert repo.branches[original_branch].commit.hexsha == original_head
    assert not any(branch.name.startswith("docsmith/") for branch in repo.branches)
    assert (root / "README.md").read_text(encoding="utf-8") == before

    proposals = await client.get(f"/api/sessions/{session_id}/changes")
    assert proposals.status_code == 200
    assert len(proposals.json()) == 1
    changes = proposals.json()[0]["changes"]
    assert len(changes) == 1
    change = changes[0]
    assert change["status"] == "pending"
    assert change["original_content"] == before
    assert change["proposed_content"] == after

    approved = await client.post(f"/api/changes/{change['id']}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "applied"
    assert all(hunk["status"] == "applied" for hunk in approved.json()["hunks"])
    saved = await client.get(f"/api/sessions/{session_id}/changes")
    assert saved.json()[0]["changes"][0] == approved.json()
    detail_response = await client.get(f"/api/sessions/{session_id}")
    branch = detail_response.json()["branch_name"]
    assert branch == f"docsmith/{session_id}"
    commit = repo.commit(f"refs/heads/{branch}")
    assert commit.hexsha != original_head
    assert [parent.hexsha for parent in commit.parents] == [original_head]
    assert (commit.tree / "README.md").data_stream.read().decode("utf-8") == after
    assert repo.active_branch.name == original_branch
    assert repo.head.commit.hexsha == original_head
    assert (root / "README.md").read_text(encoding="utf-8") == before
    assert not repo.is_dirty(untracked_files=True)
    with Repo(app.state.settings.worktrees_dir / str(session_id)) as worktree:
        assert worktree.active_branch.name == branch
        assert worktree.head.commit.hexsha == commit.hexsha
        assert not worktree.is_dirty(untracked_files=True)
