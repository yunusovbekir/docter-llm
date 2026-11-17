"""Helpers for building temporary git repositories in tests."""

from pathlib import Path
from typing import Protocol

from git import Actor, Repo

TEST_ACTOR = Actor("Docter Tests", "tests@docter.invalid")


class GitRepoFactory(Protocol):
    """Creates a git repository under the test's temporary directory."""

    def __call__(self, name: str = "repo", *, commit: bool = True, bare: bool = False) -> Repo:
        """Create repository ``name`` on branch ``main``, optionally with one commit."""
        ...


def init_repo(path: Path, *, commit: bool = True, bare: bool = False) -> Repo:
    """Initialise a repository at ``path`` on branch ``main``."""
    repo = Repo.init(path, bare=bare, initial_branch="main")
    if commit and not bare:
        (path / "README.md").write_text("# Demo\n", encoding="utf-8")
        repo.index.add(["README.md"])
        repo.index.commit("Initial commit", author=TEST_ACTOR, committer=TEST_ACTOR)
    return repo
