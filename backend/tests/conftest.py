"""Shared fixtures: isolated data directory, in-memory keyring, settings, app, and client."""

import logging
import os
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

# Keep LiteLLM offline on import (it otherwise fetches its model cost map).
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import keyring
import pytest
from fastapi import FastAPI
from git import Repo
from httpx import ASGITransport, AsyncClient

from backend.config import Settings, get_settings
from backend.main import create_app
from backend.tests.fakes import InMemoryKeyring
from backend.tests.git_utils import GitRepoFactory, init_repo

STUB_MCP_SERVER = Path(__file__).parent / "fixtures" / "stub_mcp_server.py"


@pytest.fixture
def stub_mcp_command() -> list[str]:
    """Command that runs the stub MCP server with the current interpreter."""
    return [sys.executable, str(STUB_MCP_SERVER)]


@pytest.fixture(autouse=True)
def memory_keyring() -> Iterator[InMemoryKeyring]:
    """Replace the OS keyring for every test so real credentials are never touched."""
    previous = keyring.get_keyring()
    backend = InMemoryKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(previous)


@pytest.fixture
def make_git_repo(tmp_path: Path) -> Iterator[GitRepoFactory]:
    """Factory for git repositories under ``tmp_path / "repos"``."""
    created: list[Repo] = []

    def factory(name: str = "repo", *, commit: bool = True, bare: bool = False) -> Repo:
        repo = init_repo(tmp_path / "repos" / name, commit=commit, bare=bare)
        created.append(repo)
        return repo

    yield factory
    for repo in created:
        repo.close()


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point ``DOCTER_DATA_DIR`` at a fresh temporary directory."""
    path = tmp_path / "docter-data"
    monkeypatch.setenv("DOCTER_DATA_DIR", str(path))
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()


@pytest.fixture
def frontend_dist_dir(tmp_path: Path) -> Path:
    """Location for a built frontend; absent unless a test creates it."""
    return tmp_path / "frontend-dist"


@pytest.fixture
def settings(data_dir: Path, frontend_dist_dir: Path) -> Settings:
    """Settings bound to the temporary data directory."""
    return Settings(data_dir=data_dir, frontend_dist_dir=frontend_dist_dir)


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    """Application instance (lifespan not yet started)."""
    return create_app(settings)


@pytest.fixture
def clean_log_handlers() -> Iterator[None]:
    """Remove the root log handlers that an app lifespan installs."""
    yield
    _remove_docter_log_handlers()


@pytest.fixture
async def client(app: FastAPI, clean_log_handlers: None) -> AsyncIterator[AsyncClient]:
    """HTTP client for ``app`` with the lifespan running."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=transport, base_url="http://test") as http_client,
    ):
        yield http_client


def _remove_docter_log_handlers() -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, Settings.HANDLER_MARKER, False):
            root.removeHandler(handler)
            handler.close()
