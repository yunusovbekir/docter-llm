from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from git import Repo
from httpx import AsyncClient

from backend.config import Settings
from backend.tests.git_utils import GitRepoFactory


def _path(repo: Repo) -> Path:
    assert repo.working_tree_dir is not None
    return Path(repo.working_tree_dir).resolve()


async def _create(client: AsyncClient, path: Path | str) -> dict[str, Any]:
    response = await client.post("/api/projects", json={"path": str(path)})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def test_create_registers_clean_repository(
    client: AsyncClient, make_git_repo: GitRepoFactory
) -> None:
    path = _path(make_git_repo("my-lib"))

    response = await client.post("/api/projects", json={"path": str(path)})

    assert response.status_code == 201
    body = response.json()
    assert isinstance(body["id"], int)
    assert body["name"] == "my-lib"
    assert body["path"] == str(path)
    assert body["current_branch"] == "main"
    assert body["index_status"] == "idle"
    assert body["indexed_at"] is None
    assert datetime.fromisoformat(body["created_at"]).tzinfo is not None
    assert body["is_dirty"] is False
    assert body["warnings"] == []


async def test_create_reports_dirty_tree_without_blocking(
    client: AsyncClient, make_git_repo: GitRepoFactory
) -> None:
    path = _path(make_git_repo())
    (path / "README.md").write_text("uncommitted\n", encoding="utf-8")

    body = await _create(client, path)

    assert body["is_dirty"] is True
    assert body["warnings"] == [Settings.DIRTY_WARNING]


async def test_create_detects_current_branch(
    client: AsyncClient, make_git_repo: GitRepoFactory
) -> None:
    repo = make_git_repo()
    repo.git.checkout("-b", "docs/update")

    body = await _create(client, _path(repo))

    assert body["current_branch"] == "docs/update"


async def test_create_with_detached_head(
    client: AsyncClient, make_git_repo: GitRepoFactory
) -> None:
    repo = make_git_repo()
    repo.git.checkout("--detach")

    assert (await _create(client, _path(repo)))["current_branch"] is None


async def test_create_stores_absolute_resolved_path(
    client: AsyncClient, make_git_repo: GitRepoFactory, tmp_path: Path
) -> None:
    path = _path(make_git_repo())
    link = tmp_path / "link-to-repo"
    link.symlink_to(path, target_is_directory=True)

    body = await _create(client, f"{tmp_path}/./link-to-repo/")

    assert body["path"] == str(path)


async def test_create_expands_home(
    client: AsyncClient,
    make_git_repo: GitRepoFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _path(make_git_repo("home-repo"))
    monkeypatch.setenv("HOME", str(tmp_path))

    body = await _create(client, "~/repos/home-repo")

    assert body["path"] == str(path)


async def test_create_duplicate_path_conflicts(
    client: AsyncClient, make_git_repo: GitRepoFactory, tmp_path: Path
) -> None:
    path = _path(make_git_repo())
    await _create(client, path)
    link = tmp_path / "alias"
    link.symlink_to(path, target_is_directory=True)

    for candidate in (path, link):
        response = await client.post("/api/projects", json={"path": str(candidate)})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "project_exists"


async def test_create_rejects_relative_path(client: AsyncClient) -> None:
    response = await client.post("/api/projects", json={"path": "some/relative/repo"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_path"


async def test_create_rejects_missing_path(client: AsyncClient, tmp_path: Path) -> None:
    response = await client.post("/api/projects", json={"path": str(tmp_path / "nope")})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "path_not_found"


async def test_create_rejects_file(client: AsyncClient, tmp_path: Path) -> None:
    file_path = tmp_path / "file.txt"
    file_path.write_text("x", encoding="utf-8")

    response = await client.post("/api/projects", json={"path": str(file_path)})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "not_a_directory"


async def test_create_rejects_non_git_directory(client: AsyncClient, tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    response = await client.post("/api/projects", json={"path": str(plain)})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "not_a_git_repo"


async def test_create_rejects_repository_subdirectory(
    client: AsyncClient, make_git_repo: GitRepoFactory
) -> None:
    sub = _path(make_git_repo()) / "docs"
    sub.mkdir()

    response = await client.post("/api/projects", json={"path": str(sub)})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "not_repository_root"


async def test_create_rejects_bare_repository(
    client: AsyncClient, make_git_repo: GitRepoFactory, tmp_path: Path
) -> None:
    make_git_repo("bare.git", bare=True)

    response = await client.post(
        "/api/projects", json={"path": str(tmp_path / "repos" / "bare.git")}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "not_a_git_repo"


@pytest.mark.parametrize(
    "payload",
    [{}, {"path": ""}, {"path": "   "}, {"path": None}, {"path": "/x", "name": "extra"}],
)
async def test_create_rejects_invalid_payload(client: AsyncClient, payload: dict[str, Any]) -> None:
    response = await client.post("/api/projects", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_list_projects(client: AsyncClient, make_git_repo: GitRepoFactory) -> None:
    assert (await client.get("/api/projects")).json() == []

    first = await _create(client, _path(make_git_repo("a")))
    second = await _create(client, _path(make_git_repo("b")))

    response = await client.get("/api/projects")

    assert response.status_code == 200
    assert [p["id"] for p in response.json()] == [first["id"], second["id"]]
    assert "is_dirty" not in response.json()[0]


async def test_get_project(client: AsyncClient, make_git_repo: GitRepoFactory) -> None:
    created = await _create(client, _path(make_git_repo()))

    response = await client.get(f"/api/projects/{created['id']}")

    assert response.status_code == 200
    expected = {k: v for k, v in created.items() if k not in {"is_dirty", "warnings"}}
    assert response.json() == expected


async def test_get_unknown_project_returns_404(client: AsyncClient) -> None:
    response = await client.get("/api/projects/999")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "project_not_found"


async def test_get_with_non_integer_id_is_validation_error(client: AsyncClient) -> None:
    response = await client.get("/api/projects/abc")

    assert response.status_code == 422


async def test_delete_removes_record_but_not_files(
    client: AsyncClient, make_git_repo: GitRepoFactory
) -> None:
    path = _path(make_git_repo())
    created = await _create(client, path)

    response = await client.delete(f"/api/projects/{created['id']}")

    assert response.status_code == 204
    assert response.content == b""
    assert (path / "README.md").read_text(encoding="utf-8") == "# Demo\n"
    assert (path / ".git").is_dir()
    assert (await client.get(f"/api/projects/{created['id']}")).status_code == 404
    assert (await client.get("/api/projects")).json() == []

    again = await client.delete(f"/api/projects/{created['id']}")
    assert again.status_code == 404
    assert again.json()["error"]["code"] == "project_not_found"

    assert (await _create(client, path))["path"] == str(path)
