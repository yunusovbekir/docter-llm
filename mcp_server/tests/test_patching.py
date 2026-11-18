"""Read-only patch proposals, checked and applied with Git in disposable repositories."""

import shutil
import subprocess
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from test_foundation import connected, error_text

from docter_contract import Edit, ErrorCode, ProposePatchInput, ProposePatchOutput, ToolErrorInfo
from docter_mcp.config import Config
from docter_mcp.patching import propose_patch


def snapshot(repo: Path) -> dict[Path, bytes | None]:
    return {
        path: path.read_bytes() if path.is_file() and not path.is_symlink() else None
        for path in repo.rglob("*")
    }


def proposal(
    repo: Path,
    file_path: str = "example.txt",
    *,
    edits: list[Edit] | None = None,
    new_content: str | None = None,
    config: Config | None = None,
) -> ProposePatchOutput:
    before = snapshot(repo)
    try:
        return propose_patch(
            ProposePatchInput(
                repo=str(repo),
                file_path=file_path,
                rationale="Improve documentation.",
                edits=edits,
                new_content=new_content,
            ),
            config or Config(),
        )
    finally:
        assert snapshot(repo) == before


def assert_applies(repo: Path, output: ProposePatchOutput, expected: bytes) -> None:
    before = snapshot(repo)
    result = subprocess.run(
        ["git", "-C", str(repo), "apply", "--check"],
        input=output.patch.encode("utf-8"),
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode()
    assert snapshot(repo) == before
    applied = repo.parent / "applied"
    shutil.copytree(repo, applied, symlinks=True)
    subprocess.run(
        ["git", "-C", str(applied), "apply"],
        input=output.patch.encode("utf-8"),
        capture_output=True,
        check=True,
    )
    assert (applied / output.file_path).read_bytes() == expected


def test_single_edit(repo: Path) -> None:
    (repo / "example.txt").write_bytes(b"first\nold\nlast\n")
    output = proposal(repo, edits=[Edit(old_str="old", new_str="new")])
    assert output.file_path == "example.txt"
    assert output.rationale == "Improve documentation."
    assert output.is_new_file is False
    assert "--- a/example.txt\n+++ b/example.txt\n" in output.patch
    assert "-old\n+new\n" in output.patch
    assert_applies(repo, output, b"first\nnew\nlast\n")


def test_multiple_edits_apply_in_order(repo: Path) -> None:
    (repo / "example.txt").write_bytes(b"first\nold\nlast\n")
    output = proposal(
        repo,
        edits=[
            Edit(old_str="old", new_str="intermediate"),
            Edit(old_str="intermediate", new_str="new\nmore"),
            Edit(old_str="last\n", new_str=""),
        ],
    )
    assert_applies(repo, output, b"first\nnew\nmore\n")


@pytest.mark.parametrize("content", ["", "same", "same\n", "same\r\n"])
def test_unchanged_content_returns_empty_diff(repo: Path, content: str) -> None:
    (repo / "example.txt").write_bytes(content.encode())
    output = proposal(repo, new_content=content)
    assert output.patch == ""
    assert output.is_new_file is False


def test_cancelling_edits_return_empty_diff(repo: Path) -> None:
    (repo / "example.txt").write_bytes(b"old\n")
    output = proposal(
        repo,
        edits=[Edit(old_str="old", new_str="new"), Edit(old_str="new", new_str="old")],
    )
    assert output.patch == ""
    assert output.is_new_file is False


@pytest.mark.parametrize(
    ("source", "edits", "code", "context"),
    [
        (
            "one\nsame\ntwo\nsame\n",
            [Edit(old_str="same", new_str="new")],
            ErrorCode.EDIT_AMBIGUOUS,
            "line 4",
        ),
        ("aaa\n", [Edit(old_str="aa", new_str="b")], ErrorCode.EDIT_AMBIGUOUS, "line 1"),
        (
            "one\nold value\nthree\n",
            [Edit(old_str="old vlaue", new_str="new")],
            ErrorCode.EDIT_NOT_FOUND,
            "old value",
        ),
        ("", [Edit(old_str="absent", new_str="new")], ErrorCode.EDIT_NOT_FOUND, "line 1"),
        (
            "old\n",
            [Edit(old_str="old", new_str="new"), Edit(old_str="old", new_str="again")],
            ErrorCode.EDIT_NOT_FOUND,
            "new",
        ),
    ],
)
def test_edit_errors_include_retry_context_and_never_write(
    repo: Path, source: str, edits: list[Edit], code: ErrorCode, context: str
) -> None:
    (repo / "example.txt").write_bytes(source.encode())
    with pytest.raises(ToolError) as error:
        proposal(repo, edits=edits)
    info = ToolErrorInfo.parse(str(error.value))
    assert info is not None
    assert info.code is code
    assert "example.txt" in info.message
    assert "edit " in info.message
    assert context in info.message
    assert len(info.message) < 500


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (b"old\n", b"new\n"),
        (b"old", b"new"),
        (b"old", b"new\n"),
        (b"old\n", b"new"),
        (b"old\r\n", b"new\r\n"),
        (b"old\r\nlast", b"new\r\nlast"),
        (b"old\r\nlast\r\n", b"new\r\nlast\r\n"),
        (b"old\nlast\r\n", b"new\nlast\r\n"),
        (b"old\r\n", b"new\n"),
        (b"", b"new\n"),
        (b"old\n", b""),
        (b"old\r", b"new\r"),
        (b"old\vtext\n", b"new\vtext\n"),
        ("old\u2028text\n".encode(), "new\u2028text\n".encode()),
        ("old\ncaf\u00e9\n".encode(), "new\ncaf\u00e9\n".encode()),
    ],
)
def test_full_content_preserves_exact_line_endings(repo: Path, before: bytes, after: bytes) -> None:
    (repo / "example.txt").write_bytes(before)
    output = proposal(repo, new_content=after.decode())
    assert output.is_new_file is False
    assert_applies(repo, output, after)


@pytest.mark.parametrize("ending", ["\n", "\r\n", ""])
def test_edits_preserve_line_endings_and_final_newline(repo: Path, ending: str) -> None:
    before = f"first\r\nold\r\nlast{ending}"
    after = f"first\r\nnew\r\nlast{ending}"
    (repo / "example.txt").write_bytes(before.encode())
    output = proposal(repo, edits=[Edit(old_str="old", new_str="new")])
    assert_applies(repo, output, after.encode())


def test_multiline_crlf_edit_is_exact(repo: Path) -> None:
    (repo / "example.txt").write_bytes(b"first\r\nold\r\nlast\r\n")
    output = proposal(repo, edits=[Edit(old_str="old\r\nlast", new_str="new\r\nlast")])
    assert_applies(repo, output, b"first\r\nnew\r\nlast\r\n")


def test_lf_edit_does_not_match_crlf_text(repo: Path) -> None:
    (repo / "example.txt").write_bytes(b"old\r\nlast\r\n")
    with pytest.raises(ToolError, match="EDIT_NOT_FOUND"):
        proposal(repo, edits=[Edit(old_str="old\nlast", new_str="new")])


@pytest.mark.parametrize("content", ["new\n", "new", "new\r\n", ""])
def test_new_files_in_missing_directories(repo: Path, content: str) -> None:
    output = proposal(repo, "new/nested/page.md", new_content=content)
    assert output.is_new_file is True
    assert output.file_path == "new/nested/page.md"
    assert "new file mode 100644\n" in output.patch
    if content:
        assert "--- /dev/null\n+++ b/new/nested/page.md\n" in output.patch
    assert not (repo / "new").exists()
    assert_applies(repo, output, content.encode())


@pytest.mark.parametrize("is_new_file", [False, True])
@pytest.mark.parametrize(
    "path",
    ["space name.md", "tab\tname.md", "line\nname.md", 'quote"name.md', "caf\u00e9.md"],
)
def test_git_path_quoting(repo: Path, path: str, is_new_file: bool) -> None:
    if not is_new_file:
        (repo / path).write_bytes(b"old\n")
    output = proposal(repo, path, new_content="new\n")
    assert output.is_new_file is is_new_file
    assert_applies(repo, output, b"new\n")


def test_separated_hunks_preserve_missing_final_newline(repo: Path) -> None:
    before = "\n".join(f"line {number}" for number in range(30))
    (repo / "example.txt").write_bytes(before.encode())
    output = proposal(
        repo,
        edits=[
            Edit(old_str="line 0\n", new_str="first\n"),
            Edit(old_str="line 29", new_str="last"),
        ],
    )
    assert output.patch.count("\n@@ ") == 2
    assert "\\ No newline at end of file\n" in output.patch
    assert_applies(
        repo, output, before.replace("line 0\n", "first\n").replace("line 29", "last").encode()
    )


def test_new_file_requires_full_content(repo: Path) -> None:
    with pytest.raises(ToolError, match="FILE_NOT_FOUND"):
        proposal(repo, edits=[Edit(old_str="old", new_str="new")])


@pytest.mark.parametrize(
    "path",
    ["../outside.md", "/tmp/outside.md", ".git/config", ".GIT/new", "docs\\page.md"],
)
def test_unsafe_paths_rejected_even_for_new_files(repo: Path, path: str) -> None:
    with pytest.raises(ToolError, match="PATH_OUTSIDE_REPO"):
        proposal(repo, path, new_content="new\n")


def test_symlinks_cannot_escape_or_access_git(repo: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / "escape").symlink_to(outside, target_is_directory=True)
    (repo / "metadata").symlink_to(repo / ".git", target_is_directory=True)
    for path in ["escape/new.md", "metadata/config"]:
        with pytest.raises(ToolError, match="PATH_OUTSIDE_REPO"):
            proposal(repo, path, new_content="new\n")
    assert list(outside.iterdir()) == []


def test_internal_symlink_proposes_patch_for_resolved_target(repo: Path) -> None:
    (repo / "example.txt").write_bytes(b"old\n")
    (repo / "alias.txt").symlink_to("example.txt")
    output = proposal(repo, "alias.txt", new_content="new\n")
    assert output.file_path == "example.txt"
    assert_applies(repo, output, b"new\n")


def test_directories_are_not_replaced(repo: Path) -> None:
    with pytest.raises(ToolError, match="FILE_NOT_FOUND"):
        proposal(repo, "docs", new_content="new\n")


def test_allowed_roots_enforced(repo: Path, tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="PATH_OUTSIDE_REPO"):
        proposal(repo, new_content="new\n", config=Config(allowed_roots=(tmp_path / "elsewhere",)))


@pytest.mark.parametrize("content", [b"\xff", b"\x80"])
def test_non_utf8_files_fail_explicitly(repo: Path, content: bytes) -> None:
    (repo / "example.txt").write_bytes(content)
    with pytest.raises(ToolError, match="Cannot read UTF-8"):
        proposal(repo, new_content="new\n")


@pytest.mark.parametrize(
    "change", [{"edits": [{"old_str": "old", "new_str": "new"}]}, {"new_content": "new\r\n"}]
)
async def test_mcp_patch_output(repo: Path, change: dict[str, object]) -> None:
    (repo / "example.txt").write_bytes(b"old\r\n")
    before = snapshot(repo)
    async with connected(repo) as session:
        result = await session.call_tool(
            "propose_patch",
            {"repo": str(repo), "file_path": "example.txt", "rationale": "Improve docs", **change},
        )
    assert not result.is_error
    output = ProposePatchOutput.model_validate(result.structured_content)
    assert output.rationale == "Improve docs"
    assert snapshot(repo) == before
    assert_applies(repo, output, b"new\r\n")


async def test_mcp_edit_error_preserves_contract_code(repo: Path) -> None:
    (repo / "example.txt").write_bytes(b"old\n")
    async with connected(repo) as session:
        result = await session.call_tool(
            "propose_patch",
            {
                "repo": str(repo),
                "file_path": "example.txt",
                "rationale": "Improve docs",
                "edits": [{"old_str": "missing", "new_str": "new"}],
            },
        )
    info = ToolErrorInfo.parse(error_text(result))
    assert info is not None
    assert info.code is ErrorCode.EDIT_NOT_FOUND
    assert "line 1" in info.message


@pytest.mark.parametrize(
    "change",
    [
        {},
        {"edits": [], "new_content": "new"},
        {"edits": [{"old_str": "old", "new_str": "new"}], "new_content": "new"},
        {"edits": [{"old_str": "", "new_str": "new"}]},
    ],
)
async def test_mcp_rejects_invalid_changes(repo: Path, change: dict[str, object]) -> None:
    async with connected(repo) as session:
        result = await session.call_tool(
            "propose_patch",
            {"repo": str(repo), "file_path": "example.txt", "rationale": "Improve docs", **change},
        )
    assert result.is_error
