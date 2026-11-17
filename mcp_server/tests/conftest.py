"""Shared fixtures for the MCP server tests."""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def sample_repo() -> Path:
    """Root of the sample library under ``tests/fixtures/sample_repo``."""
    return REPO_ROOT / "tests" / "fixtures" / "sample_repo"


@pytest.fixture
def repo(tmp_path: Path, sample_repo: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(sample_repo, root)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root
