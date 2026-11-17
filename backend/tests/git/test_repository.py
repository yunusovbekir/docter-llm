from pathlib import Path

import pytest

from backend.git.repository import (
    NotAGitRepositoryError,
    NotRepositoryRootError,
    RepositoryInfo,
    inspect_repository,
)
from backend.tests.git_utils import GitRepoFactory


def _root(make_git_repo: GitRepoFactory, *, commit: bool = True) -> Path:
    repo = make_git_repo(commit=commit)
    assert repo.working_tree_dir is not None
    return Path(repo.working_tree_dir).resolve()


def test_clean_repository(make_git_repo: GitRepoFactory) -> None:
    root = _root(make_git_repo)

    assert inspect_repository(root) == RepositoryInfo(
        root=root, current_branch="main", is_dirty=False
    )


def test_modified_tracked_file_is_dirty(make_git_repo: GitRepoFactory) -> None:
    root = _root(make_git_repo)
    (root / "README.md").write_text("changed\n", encoding="utf-8")

    assert inspect_repository(root).is_dirty


def test_staged_change_is_dirty(make_git_repo: GitRepoFactory) -> None:
    repo = make_git_repo()
    root = Path(str(repo.working_tree_dir)).resolve()
    (root / "new.md").write_text("new\n", encoding="utf-8")
    repo.index.add(["new.md"])

    assert inspect_repository(root).is_dirty


def test_untracked_file_is_dirty(make_git_repo: GitRepoFactory) -> None:
    root = _root(make_git_repo)
    (root / "notes.txt").write_text("untracked\n", encoding="utf-8")

    assert inspect_repository(root).is_dirty


def test_detects_checked_out_branch(make_git_repo: GitRepoFactory) -> None:
    repo = make_git_repo()
    repo.git.checkout("-b", "feature/docs")

    info = inspect_repository(Path(str(repo.working_tree_dir)).resolve())

    assert info.current_branch == "feature/docs"


def test_detached_head_has_no_branch(make_git_repo: GitRepoFactory) -> None:
    repo = make_git_repo()
    repo.git.checkout("--detach")

    info = inspect_repository(Path(str(repo.working_tree_dir)).resolve())

    assert info.current_branch is None


def test_repository_without_commits(make_git_repo: GitRepoFactory) -> None:
    root = _root(make_git_repo, commit=False)

    assert inspect_repository(root) == RepositoryInfo(
        root=root, current_branch="main", is_dirty=False
    )


def test_plain_directory_is_rejected(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    with pytest.raises(NotAGitRepositoryError):
        inspect_repository(plain.resolve())


def test_subdirectory_is_rejected_with_root_hint(make_git_repo: GitRepoFactory) -> None:
    root = _root(make_git_repo)
    sub = root / "docs"
    sub.mkdir()

    with pytest.raises(NotRepositoryRootError, match=str(root)):
        inspect_repository(sub)


def test_bare_repository_is_rejected(make_git_repo: GitRepoFactory, tmp_path: Path) -> None:
    make_git_repo("bare.git", bare=True)

    with pytest.raises(NotAGitRepositoryError, match="bare"):
        inspect_repository((tmp_path / "repos" / "bare.git").resolve())
