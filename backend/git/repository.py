"""Read-only inspection of a local git repository.

These functions are synchronous (GitPython spawns ``git`` subprocesses); call them
from async code with ``asyncio.to_thread``.
"""

from dataclasses import dataclass
from pathlib import Path

from git import GitCommandError, InvalidGitRepositoryError, NoSuchPathError, Repo

from backend.errors import DocterError


class NotAGitRepositoryError(DocterError):
    """The directory is not a (non-bare) git working tree."""

    code = "not_a_git_repo"


class NotRepositoryRootError(DocterError):
    """The directory is inside a git repository but is not its root."""

    code = "not_repository_root"


class GitOperationError(DocterError):
    """A Git command could not complete a repository operation."""

    code = "git_error"


@dataclass(frozen=True, slots=True)
class RepositoryInfo:
    """State of a repository's working tree."""

    root: Path
    current_branch: str | None
    is_dirty: bool


def inspect_repository(path: Path) -> RepositoryInfo:
    """Inspect the git working tree rooted at ``path`` (absolute and resolved).

    ``current_branch`` is ``None`` for a detached HEAD. ``is_dirty`` includes
    untracked files.

    Raises:
        NotAGitRepositoryError: ``path`` is not a git repository, or is a bare one.
        NotRepositoryRootError: ``path`` is a subdirectory of a repository.
        GitOperationError: git failed to read the repository state.
    """
    try:
        repo = Repo(path)
    except (InvalidGitRepositoryError, NoSuchPathError) as exc:
        parent_root = _find_enclosing_root(path)
        if parent_root is not None:
            raise NotRepositoryRootError(
                f"{path} is inside the git repository at {parent_root}; "
                "register the repository root instead"
            ) from exc
        raise NotAGitRepositoryError(f"{path} is not a git repository") from exc

    with repo:
        if repo.bare or repo.working_tree_dir is None:
            raise NotAGitRepositoryError(
                f"{path} is a bare git repository; a working tree is required"
            )
        root = Path(repo.working_tree_dir).resolve()
        if root != path:
            raise NotRepositoryRootError(
                f"{path} is not the root of its git repository ({root}); "
                "register the repository root instead"
            )
        try:
            branch = None if repo.head.is_detached else repo.active_branch.name
            dirty = repo.is_dirty(untracked_files=True)
        except GitCommandError as exc:
            raise GitOperationError(f"git failed to read {path}: {exc.stderr.strip()}") from exc

    return RepositoryInfo(root=root, current_branch=branch, is_dirty=dirty)


def _find_enclosing_root(path: Path) -> Path | None:
    try:
        with Repo(path, search_parent_directories=True) as repo:
            return Path(repo.working_tree_dir).resolve() if repo.working_tree_dir else None
    except (InvalidGitRepositoryError, NoSuchPathError):
        return None
