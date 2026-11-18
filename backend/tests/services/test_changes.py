"""Database proposals validate against real Git repositories without modifying them."""

from collections.abc import AsyncIterator
from difflib import unified_diff
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlmodel import select

from backend.agent.changes import Error, Ok
from backend.models import Change, ChangeHunk, ChangeSet, Project, Session
from backend.models.change import ChangeStatus
from backend.services.changes import DbChangeSink, list_change_sets
from backend.tests.git_utils import GitRepoFactory
from docter_contract import ProposePatchOutput

PATCH = "--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-# Demo\n+# Documentation\n"


@pytest.fixture
async def proposal(
    app: FastAPI, client: AsyncClient, make_git_repo: GitRepoFactory
) -> AsyncIterator[tuple[DbChangeSink, Path]]:
    repo = make_git_repo()
    assert repo.working_tree_dir is not None
    async with app.state.sessionmaker() as db:
        project = Project(name="example", path=str(repo.working_tree_dir))
        db.add(project)
        await db.flush()
        assert project.id is not None
        session = Session(project_id=project.id, mode="add")
        db.add(session)
        await db.flush()
        assert session.id is not None
        change_set = ChangeSet(session_id=session.id, run_id="run")
        db.add(change_set)
        await db.commit()
        assert change_set.id is not None
    yield DbChangeSink(app.state.sessionmaker, change_set.id), Path(repo.working_tree_dir)


def _change(
    patch: str = PATCH, *, path: str = "README.md", new: bool = False
) -> ProposePatchOutput:
    return ProposePatchOutput(
        file_path=path, patch=patch, rationale="Improve documentation", is_new_file=new
    )


async def test_valid_patch_is_persisted_atomically_without_applying(
    app: FastAPI, proposal: tuple[DbChangeSink, Path]
) -> None:
    sink, root = proposal
    index = (root / ".git" / "index").read_bytes()

    assert isinstance(await sink.add(_change()), Ok)

    assert (root / "README.md").read_text() == "# Demo\n"
    assert (root / ".git" / "index").read_bytes() == index
    async with app.state.sessionmaker() as db:
        change = (await db.exec(select(Change))).one()
        assert change.file_path == "README.md"
        assert change.patch == PATCH
        assert change.rationale == "Improve documentation"
        assert change.status is ChangeStatus.PENDING
        assert change.parent_change_id is None
        assert change.created_at.tzinfo is not None
        assert change.original_content == "# Demo\n"
        assert change.proposed_content == "# Documentation\n"
        hunk = (await db.exec(select(ChangeHunk))).one()
        assert hunk.change_id == change.id
        assert hunk.index == 0
        assert hunk.header == "@@ -1 +1 @@"
        assert hunk.status is ChangeStatus.PENDING


@pytest.mark.parametrize(
    "path",
    [
        "../outside.md",
        "docs/../../outside.md",
        "/tmp/outside.md",
        r"C:\outside.md",
        "C:outside.md",
        r"..\outside.md",
        ".git/config",
        "docs/.GiT/config",
        ".",
        "bad\0path",
    ],
)
async def test_unsafe_paths_are_retryable_and_not_stored(
    app: FastAPI, proposal: tuple[DbChangeSink, Path], path: str
) -> None:
    sink, root = proposal

    result = await sink.add(_change(path=path))

    assert isinstance(result, Error)
    assert "path" in result.message.lower()
    assert (root / "README.md").read_text() == "# Demo\n"
    async with app.state.sessionmaker() as db:
        assert (await db.exec(select(Change))).all() == []
        assert (await db.exec(select(ChangeHunk))).all() == []


@pytest.mark.parametrize("target", ["../outside.md", ".git/config", "other.md"])
async def test_patch_cannot_target_a_different_file(
    proposal: tuple[DbChangeSink, Path], target: str
) -> None:
    sink, _ = proposal
    patch = PATCH.replace("a/README.md", f"a/{target}").replace("b/README.md", f"b/{target}")

    assert isinstance(await sink.add(_change(patch)), Error)


@pytest.mark.parametrize(
    "patch",
    [
        "",
        "not a patch",
        PATCH.replace("@@ -1 +1 @@", "@@ -1,4 +1,4 @@"),
        PATCH.replace("-# Demo", "-stale content"),
        PATCH + PATCH.replace("README.md", "other.md"),
        "diff --git a/README.md b/README.md\nold mode 100644\nnew mode 100755\n",
        (
            "diff --git a/link b/link\nnew file mode 120000\n"
            "--- /dev/null\n+++ b/link\n@@ -0,0 +1 @@\n+/outside\n"
        ),
    ],
)
async def test_bad_patch_returns_error_then_agent_can_retry(
    app: FastAPI, proposal: tuple[DbChangeSink, Path], patch: str
) -> None:
    sink, root = proposal
    result = await sink.add(_change(patch))
    assert isinstance(result, Error)
    assert result.message
    async with app.state.sessionmaker() as db:
        assert (await db.exec(select(Change))).all() == []
        assert (await db.exec(select(ChangeHunk))).all() == []

    assert isinstance(await sink.add(_change()), Ok)
    assert (root / "README.md").read_text() == "# Demo\n"


@pytest.mark.parametrize("target", ["outside", ".git/config", "README.md"])
async def test_symlinks_are_rejected(
    proposal: tuple[DbChangeSink, Path], target: str, tmp_path: Path
) -> None:
    sink, root = proposal
    outside = tmp_path / "outside"
    outside.write_text("# Demo\n")
    (root / "link").symlink_to(outside if target == "outside" else root / target)
    result = await sink.add(_change(PATCH.replace("README.md", "link"), path="link"))
    assert isinstance(result, Error)
    assert outside.read_text() == "# Demo\n"


async def test_multiple_hunks_are_parsed_and_snapshots_survive_file_changes(
    app: FastAPI, proposal: tuple[DbChangeSink, Path]
) -> None:
    sink, root = proposal
    before = "".join(f"line {number}\n" for number in range(1, 31))
    after = before.replace("line 2\n", "second line\n").replace("line 25\n", "better line\n")
    (root / "guide.md").write_text(before)
    patch = "".join(
        unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="a/guide.md",
            tofile="b/guide.md",
        )
    )
    assert isinstance(await sink.add(_change(patch, path="guide.md")), Ok)
    (root / "guide.md").write_text("Changed after proposal\n")

    async with app.state.sessionmaker() as db:
        change_set = (await db.exec(select(ChangeSet))).one()
        result = await list_change_sets(db, change_set.session_id)
    saved = result[0].changes[0]
    assert saved.original_content == before
    assert saved.proposed_content == after
    assert [hunk.index for hunk in saved.hunks] == [0, 1]
    assert [hunk.header for hunk in saved.hunks] == [
        line for line in patch.splitlines() if line.startswith("@@")
    ]


@pytest.mark.parametrize(
    ("before", "after", "patch", "new"),
    [
        (
            None,
            "New docs\n",
            "diff --git a/docs/new.md b/docs/new.md\nnew file mode 100644\n"
            "--- /dev/null\n+++ b/docs/new.md\n@@ -0,0 +1 @@\n+New docs\n",
            True,
        ),
        (
            None,
            "",
            "diff --git a/docs/new.md b/docs/new.md\nnew file mode 100644\n",
            True,
        ),
        (
            "old",
            "new",
            "--- a/docs/new.md\n+++ b/docs/new.md\n@@ -1 +1 @@\n"
            "-old\n\\ No newline at end of file\n+new\n\\ No newline at end of file\n",
            False,
        ),
        (
            "old\r\n",
            "new\r\n",
            "--- a/docs/new.md\n+++ b/docs/new.md\n@@ -1 +1 @@\n-old\r\n+new\r\n",
            False,
        ),
        (
            "old\n",
            "",
            "--- a/docs/new.md\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n",
            False,
        ),
    ],
)
async def test_diff_snapshots_preserve_new_deleted_empty_and_nonterminated_files(
    app: FastAPI,
    proposal: tuple[DbChangeSink, Path],
    before: str | None,
    after: str,
    patch: str,
    new: bool,
) -> None:
    sink, root = proposal
    target = root / "docs" / "new.md"
    if before is not None:
        target.parent.mkdir()
        target.write_bytes(before.encode())

    result = await sink.add(_change(patch, path="docs/new.md", new=new))

    assert isinstance(result, Ok), result
    assert target.exists() is (before is not None)
    if before is not None:
        assert target.read_bytes() == before.encode()
    async with app.state.sessionmaker() as db:
        saved = (await db.exec(select(Change))).one()
        assert saved.original_content == (before or "")
        assert saved.proposed_content == after


async def test_preview_uses_git_offsets_and_ignores_external_git_environment(
    app: FastAPI, proposal: tuple[DbChangeSink, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    sink, root = proposal
    (root / "README.md").write_text("introduction\nbefore\n# Demo\nafter\ntail\n")
    monkeypatch.setenv("GIT_DIR", str(root / "nonexistent"))
    monkeypatch.setenv("GIT_WORK_TREE", str(root / "nonexistent"))

    patch = (
        "--- a/README.md\n+++ b/README.md\n@@ -3,3 +3,3 @@\n"
        " before\n-# Demo\n+# Documentation\n after\n"
    )
    assert isinstance(await sink.add(_change(patch)), Ok)

    async with app.state.sessionmaker() as db:
        saved = (await db.exec(select(Change))).one()
        assert saved.original_content == "introduction\nbefore\n# Demo\nafter\ntail\n"
        assert saved.proposed_content == "introduction\nbefore\n# Documentation\nafter\ntail\n"


@pytest.mark.parametrize("new", [True, False])
async def test_creation_flag_must_match_patch(
    proposal: tuple[DbChangeSink, Path], new: bool
) -> None:
    sink, _ = proposal
    patch = PATCH if new else "--- /dev/null\n+++ b/new.md\n@@ -0,0 +1 @@\n+New\n"
    result = await sink.add(_change(patch, path="README.md" if new else "new.md", new=new))
    assert isinstance(result, Error)


@pytest.mark.parametrize("mode", ["120000", "160000"])
@pytest.mark.parametrize("metadata", ["new file mode {mode}\n", "index 0000000..1234567 {mode}\n"])
async def test_nonregular_file_modes_are_rejected(
    app: FastAPI, proposal: tuple[DbChangeSink, Path], mode: str, metadata: str
) -> None:
    sink, root = proposal
    patch = (
        "diff --git a/link b/link\n"
        + metadata.format(mode=mode)
        + "--- /dev/null\n+++ b/link\n@@ -0,0 +1 @@\n+/outside\n"
    )
    result = await sink.add(_change(patch, path="link", new=True))
    assert isinstance(result, Error)
    assert "regular text files" in result.message
    assert not (root / "link").exists()
    async with app.state.sessionmaker() as db:
        assert (await db.exec(select(Change))).all() == []


@pytest.mark.parametrize(
    ("path", "quoted"),
    [
        ("hello world.md", "hello world.md"),
        ("caf\u00e9.md", r"caf\303\251.md"),
        ("tab\tname.md", r"tab\011name.md"),
    ],
)
async def test_git_quoted_utf8_paths_are_supported(
    app: FastAPI, proposal: tuple[DbChangeSink, Path], path: str, quoted: str
) -> None:
    sink, root = proposal
    (root / path).write_text("old\n")
    patch = (
        f'diff --git "a/{quoted}" "b/{quoted}"\n'
        f'--- "a/{quoted}"\n+++ "b/{quoted}"\n@@ -1 +1 @@\n-old\n+new\n'
    )
    result = await sink.add(_change(patch, path=path))
    assert isinstance(result, Ok), result
    async with app.state.sessionmaker() as db:
        saved = (await db.exec(select(Change))).one()
        assert saved.file_path == path
        assert saved.proposed_content == "new\n"
