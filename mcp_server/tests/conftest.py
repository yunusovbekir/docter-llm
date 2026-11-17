"""Shared fixtures for the MCP server tests."""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def sample_repo() -> Path:
    """Root of the sample library under ``tests/fixtures/sample_repo``."""
    return REPO_ROOT / "tests" / "fixtures" / "sample_repo"
