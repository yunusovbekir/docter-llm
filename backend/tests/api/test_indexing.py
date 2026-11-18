import asyncio
import os
import shlex
import signal
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.config import Settings
from backend.main import create_app
from backend.services.indexing import IndexingService
from backend.tests.git_utils import GitRepoFactory


@pytest.fixture
def index_control(tmp_path: Path) -> Path:
    control = tmp_path / "index-control"
    control.mkdir()
    return control


@pytest.fixture
def settings(settings: Settings, stub_mcp_command: list[str], index_control: Path) -> Settings:
    return settings.model_copy(
        update={
            "mcp_command": shlex.join(
                [*stub_mcp_command, "--index-control-dir", str(index_control)]
            )
        }
    )


async def _create_project(
    client: AsyncClient, make_git_repo: GitRepoFactory, name: str = "repo"
) -> int:
    repo = make_git_repo(name)
    response = await client.post("/api/projects", json={"path": repo.working_tree_dir})
    assert response.status_code == 201, response.text
    project_id: int = response.json()["id"]
    return project_id


async def _wait_for(
    client: AsyncClient, project_id: int, *, status: str, message: str | None = None
) -> dict[str, Any]:
    async with asyncio.timeout(20):
        while True:
            response = await client.get(f"/api/projects/{project_id}/index/status")
            assert response.status_code == 200, response.text
            body: dict[str, Any] = response.json()
            if body["status"] == status and (message is None or body["message"] == message):
                return body
            await asyncio.sleep(0.01)


async def test_index_success_and_live_progress(
    client: AsyncClient,
    make_git_repo: GitRepoFactory,
    index_control: Path,
    app: FastAPI,
) -> None:
    project_id = await _create_project(client, make_git_repo)
    url = f"/api/projects/{project_id}/index"
    assert (await client.get(f"{url}/status")).json() == {
        "status": "idle",
        "progress": 0,
        "message": None,
        "indexed_at": None,
        "error": None,
    }

    response = await client.post(url)

    assert response.status_code == 202
    assert response.json() == {
        "status": "running",
        "progress": 0,
        "message": "Starting indexing",
        "indexed_at": None,
        "error": None,
    }
    running = await _wait_for(client, project_id, status="running", message="Indexed 1/3")
    assert running["progress"] == pytest.approx(100 / 3)
    assert running["error"] is None
    project = (await client.get(f"/api/projects/{project_id}")).json()
    assert project["index_status"] == "running"
    assert (index_control / "path").read_text() == project["path"]

    (index_control / "release").touch()
    done = await _wait_for(client, project_id, status="done")

    assert done["progress"] == 100
    assert done["message"] == "Indexing complete"
    assert done["error"] is None
    assert datetime.fromisoformat(done["indexed_at"]).tzinfo is not None
    project = (await client.get(f"/api/projects/{project_id}")).json()
    assert project["index_status"] == "done"
    assert project["indexed_at"] == done["indexed_at"]

    # A fresh service has no in-memory state and still reports the completed index.
    fresh = IndexingService(app.state.sessionmaker, app.state.mcp)
    persisted = await fresh.get_status(project_id)
    assert persisted.status == "done"
    assert persisted.progress == 100
    assert persisted.indexed_at is not None


async def test_index_tool_failure_and_retry(
    client: AsyncClient, make_git_repo: GitRepoFactory, index_control: Path
) -> None:
    project_id = await _create_project(client, make_git_repo)
    url = f"/api/projects/{project_id}/index"
    (index_control / "fail").touch()
    (index_control / "release").touch()

    assert (await client.post(url)).status_code == 202
    failed = await _wait_for(client, project_id, status="failed")

    assert "Stub indexing failed" in failed["error"]
    assert failed["message"] == "Indexing failed"
    assert 0 <= failed["progress"] <= 100
    assert failed["indexed_at"] is None
    assert (await client.get(f"/api/projects/{project_id}")).json()["index_status"] == "failed"

    (index_control / "fail").unlink()
    assert (await client.post(url)).status_code == 202
    done = await _wait_for(client, project_id, status="done")
    assert done["error"] is None
    assert done["indexed_at"] is not None

    (index_control / "fail").touch()
    assert (await client.post(url)).status_code == 202
    failed_again = await _wait_for(client, project_id, status="failed")
    assert failed_again["indexed_at"] == done["indexed_at"]


async def test_index_concurrent_requests_return_409(
    client: AsyncClient, make_git_repo: GitRepoFactory, index_control: Path
) -> None:
    project_id = await _create_project(client, make_git_repo)
    url = f"/api/projects/{project_id}/index"

    responses = await asyncio.gather(client.post(url), client.post(url))

    assert sorted(response.status_code for response in responses) == [202, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["error"]["code"] == "index_running"
    await _wait_for(client, project_id, status="running", message="Indexed 1/3")
    assert (await client.post(url)).status_code == 409
    (index_control / "release").touch()
    await _wait_for(client, project_id, status="done")
    assert (await client.post(url)).status_code == 202
    await _wait_for(client, project_id, status="done")


async def test_unknown_project_returns_404(client: AsyncClient) -> None:
    for response in (
        await client.post("/api/projects/999/index"),
        await client.get("/api/projects/999/index/status"),
    ):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "project_not_found"


async def test_different_projects_can_index_concurrently(
    client: AsyncClient, make_git_repo: GitRepoFactory, index_control: Path
) -> None:
    first = await _create_project(client, make_git_repo, "first")
    second = await _create_project(client, make_git_repo, "second")

    responses = await asyncio.gather(
        client.post(f"/api/projects/{first}/index"),
        client.post(f"/api/projects/{second}/index"),
    )

    assert [response.status_code for response in responses] == [202, 202]
    for project_id in (first, second):
        await _wait_for(client, project_id, status="running", message="Indexed 1/3")
    (index_control / "release").touch()
    for project_id in (first, second):
        await _wait_for(client, project_id, status="done")


async def test_index_server_crash_is_reported(
    client: AsyncClient, make_git_repo: GitRepoFactory, index_control: Path
) -> None:
    project_id = await _create_project(client, make_git_repo)
    assert (await client.post(f"/api/projects/{project_id}/index")).status_code == 202
    await _wait_for(client, project_id, status="running", message="Indexed 1/3")

    pid = int((index_control / "pid").read_text())
    os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    failed = await _wait_for(client, project_id, status="failed")

    assert "connection lost" in failed["error"]
    assert failed["indexed_at"] is None


async def test_index_timeout_is_reported(
    client: AsyncClient,
    make_git_repo: GitRepoFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Settings, "INDEX_TIMEOUT_S", 0.2)
    project_id = await _create_project(client, make_git_repo)

    assert (await client.post(f"/api/projects/{project_id}/index")).status_code == 202
    failed = await _wait_for(client, project_id, status="failed")

    assert "timed out" in failed["error"]
    assert failed["indexed_at"] is None


async def test_shutdown_marks_active_index_failed(
    settings: Settings, make_git_repo: GitRepoFactory, clean_log_handlers: None
) -> None:
    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        project_id = await _create_project(client, make_git_repo)
        assert (await client.post(f"/api/projects/{project_id}/index")).status_code == 202
        await _wait_for(client, project_id, status="running", message="Indexed 1/3")

    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        body = (await client.get(f"/api/projects/{project_id}/index/status")).json()
        assert body["status"] == "failed"
        assert body["indexed_at"] is None
