"""Exercise MCPManager against docter-mcp over its actual stdio transport."""

import sys
from pathlib import Path

import anyio

from backend.mcp_client.manager import MCPManager
from backend.tests.git_utils import GitRepoFactory
from docter_contract import DocGap, FindDocGapsOutput, IndexRepoOutput, ProposePatchOutput

LAUNCHER = Path(__file__).resolve().parents[1] / "fixtures" / "offline_docter_mcp.py"


async def test_real_server_index_gaps_and_patch(
    make_git_repo: GitRepoFactory, tmp_path: Path
) -> None:
    git_repo = make_git_repo()
    repo = Path(git_repo.working_dir)
    source = "def greet(name: str) -> str:\n    return f'Hello, {name}'\n"
    source_path = repo / "greeter.py"
    source_path.write_text(source, encoding="utf-8", newline="\n")
    manager = MCPManager(
        [sys.executable, str(LAUNCHER), "--repo", str(repo)],
        env={
            "DOCTER_INDEX_DIR": str(tmp_path / "index"),
            "DOCTER_ALLOWED_ROOTS": str(repo),
            "DOCSMITH_ALLOW_EXEC": "false",
        },
        startup_timeout=30,
    )
    args = {"repo": str(repo)}
    try:
        indexed = await manager.call_tool("index_repo", args, timeout=30)
        assert not indexed.is_error, indexed.text
        summary = IndexRepoOutput.model_validate(indexed.structured)
        assert summary.files_indexed == 2
        assert summary.symbols >= 1
        assert summary.chunks > summary.symbols

        found = await manager.call_tool("find_doc_gaps", args, timeout=30)
        assert not found.is_error, found.text
        gaps = FindDocGapsOutput.model_validate(found.structured)
        gap = next(item for item in gaps.items if item.qualname == "greeter.greet")
        assert gap.file == "greeter.py"
        assert DocGap.DOCSTRING in gap.missing

        updated = (
            "def greet(name: str) -> str:\n"
            '    """Build a greeting for the given name.\n\n'
            "    Args:\n"
            "        name: The name to greet.\n\n"
            "    Returns:\n"
            "        The greeting text.\n"
            '    """\n'
            "    return f'Hello, {name}'\n"
        )
        proposed = await manager.call_tool(
            "propose_patch",
            {**args, "file_path": gap.file, "new_content": updated, "rationale": "Document greet"},
            timeout=30,
        )
        assert not proposed.is_error, proposed.text
        patch = ProposePatchOutput.model_validate(proposed.structured)
        assert patch.file_path == gap.file
        assert not patch.is_new_file
        assert '+    """Build a greeting' in patch.patch
        result = await anyio.run_process(
            ["git", "apply", "--check", "-"], cwd=repo, input=patch.patch.encode(), check=False
        )
        assert result.returncode == 0, result.stderr.decode()
        assert source_path.read_bytes() == source.encode()
    finally:
        await manager.close()
