"""M2 integration tests through the SDK's in-memory client transport."""

import os
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
import pytest
import yaml
from mcp import ClientSession, StdioServerParameters
from mcp.client.session import SamplingFnT
from mcp.client.stdio import stdio_client
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError
from mcp.shared.memory import create_client_server_memory_streams
from mcp.types import CallToolResult, TextContent, TextResourceContents

from docter_contract import (
    CONTRACT_VERSION,
    RESOURCE_URIS,
    TOOL_SPECS,
    DocFramework,
    GetServerInfoOutput,
    ListDocFilesOutput,
    ReadFileOutput,
    ToolName,
)
from docter_mcp.config import Config
from docter_mcp.sandbox import Sandbox
from docter_mcp.server import create_server


@asynccontextmanager
async def connected(
    repo: Path, config: Config | None = None, sampling_callback: SamplingFnT | None = None
) -> AsyncIterator[ClientSession]:
    server = create_server(config or Config(), repo)
    async with (
        create_client_server_memory_streams() as (client_streams, server_streams),
        anyio.create_task_group() as group,
    ):
        group.start_soon(
            server._lowlevel_server.run,
            *server_streams,
            server._lowlevel_server.create_initialization_options(),
        )
        async with ClientSession(*client_streams, sampling_callback=sampling_callback) as session:
            await session.initialize()
            yield session
        group.cancel_scope.cancel()


def error_text(result: CallToolResult) -> str:
    assert result.is_error
    return "\n".join(block.text for block in result.content if isinstance(block, TextContent))


async def resource_text(session: ClientSession, uri: str) -> str:
    result = await session.read_resource(uri)
    (content,) = result.contents
    assert isinstance(content, TextResourceContents)
    return content.text


def test_config_defaults_and_overrides(tmp_path: Path) -> None:
    defaults = Config.from_env({})
    assert defaults.index_dir == Path.home() / ".docter" / "index"
    assert defaults.allowed_roots == ()
    assert defaults.allow_exec is False
    config = Config.from_env(
        {
            "DOCTER_INDEX_DIR": str(tmp_path / "index"),
            "DOCTER_ALLOWED_ROOTS": f" {tmp_path}, {tmp_path / 'second'} ",
            "DOCTER_ALLOW_EXEC": "true",
        }
    )
    assert config.index_dir == tmp_path / "index"
    assert config.allowed_roots == (tmp_path, tmp_path / "second")
    assert config.allow_exec is True
    assert not config.index_dir.exists()


@pytest.mark.parametrize(
    "values",
    [
        {"DOCTER_ALLOW_EXEC": "yes"},
        {"DOCTER_ALLOWED_ROOTS": "relative"},
        {"DOCTER_ALLOWED_ROOTS": "/tmp,"},
    ],
)
def test_config_rejects_bad_settings(values: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        Config.from_env(values)


async def test_info_and_contract_schemas(repo: Path) -> None:
    async with connected(repo, Config(allow_exec=True)) as session:
        discovered = (await session.list_tools()).tools
        assert {tool.name for tool in discovered} == {
            "get_server_info",
            "index_repo",
            "search_code",
            "get_symbol",
            "read_file",
            "list_doc_files",
            "find_doc_gaps",
            "check_doc_drift",
            "run_doc_examples",
            "propose_patch",
            "translate_file",
        }
        for tool in discovered:
            spec = TOOL_SPECS[ToolName(tool.name)]
            assert tool.input_schema == spec.input.model_json_schema()
            assert tool.output_schema == spec.output.model_json_schema()
        result = await session.call_tool("get_server_info", {})
        assert not result.is_error
        info = GetServerInfoOutput.model_validate(result.structured_content)
        assert info.contract_version == CONTRACT_VERSION
        assert info.server_version == "0.1.0"
        assert info.capabilities.resources == list(RESOURCE_URIS)
        assert info.capabilities.tools == list(Config.TOOL_NAMES)
        assert info.capabilities.execution_enabled is True


async def test_standalone_prompts(repo: Path) -> None:
    async with connected(repo) as session:
        prompts = {prompt.name: prompt for prompt in (await session.list_prompts()).prompts}
        assert set(prompts) == {"add_docs", "fix_docs", "find_missing_docs", "translate_docs"}
        cases = {
            "add_docs": ({"scope": "src/"}, ("find_doc_gaps", "propose_patch", "src/")),
            "fix_docs": ({}, ("index_repo", "check_doc_drift", "propose_patch")),
            "find_missing_docs": ({}, ("find_doc_gaps", "Do not call propose_patch")),
            "translate_docs": (
                {"file_path": "README.md", "target_language": "pt-BR"},
                ("translate_file", "README.md", "pt-BR"),
            ),
        }
        for name, (arguments, expected) in cases.items():
            required = {
                argument.name for argument in prompts[name].arguments or [] if argument.required
            }
            assert required == (
                {"file_path", "target_language"} if name == "translate_docs" else set()
            )
            result = await session.get_prompt(name, arguments)
            (message,) = result.messages
            assert message.role == "user"
            assert isinstance(message.content, TextContent)
            for text in (str(repo), *expected):
                assert text in message.content.text


async def test_read_file_and_ranges(repo: Path) -> None:
    (repo / "lines.txt").write_bytes(b"first\r\nsecond\r\nthird")
    async with connected(repo) as session:
        for extra, expected in [
            ({}, "first\r\nsecond\r\nthird"),
            ({"start_line": 2, "end_line": 2}, "second\r\n"),
            ({"start_line": 3}, "third"),
            ({"end_line": 1}, "first\r\n"),
            ({"start_line": 20}, ""),
        ]:
            result = await session.call_tool(
                "read_file", {"repo": str(repo), "file_path": "lines.txt", **extra}
            )
            assert not result.is_error
            output = ReadFileOutput.model_validate(result.structured_content)
            assert output.content == expected
            assert output.total_lines == 3
            assert output.file_path == "lines.txt"


@pytest.mark.parametrize(
    "extra",
    [{"start_line": 0}, {"start_line": 3, "end_line": 2}, {"unexpected": True}],
)
async def test_invalid_contract_arguments(repo: Path, extra: dict[str, object]) -> None:
    async with connected(repo) as session:
        result = await session.call_tool(
            "read_file", {"repo": str(repo), "file_path": "README.md", **extra}
        )
        assert result.is_error


async def test_list_docs_and_mkdocs_precedence(repo: Path) -> None:
    (repo / "docs" / "conf.py").write_text("raise RuntimeError('never execute')")
    (repo / ".git" / "secret.md").write_text("must not be read")
    async with connected(repo) as session:
        result = await session.call_tool("list_doc_files", {"repo": str(repo)})
        assert not result.is_error
        output = ListDocFilesOutput.model_validate(result.structured_content)
        assert output.framework is DocFramework.MKDOCS
        assert [(doc.path, doc.title) for doc in output.files] == [
            ("README.md", "mylib"),
            ("docs/index.md", "mylib documentation"),
            ("docs/reference.rst", "API reference"),
        ]
        assert [doc.format.value for doc in output.files] == ["markdown", "markdown", "rst"]
        (repo / "mkdocs.yml").unlink()
        result = await session.call_tool("list_doc_files", {"repo": str(repo)})
        assert ListDocFilesOutput.model_validate(result.structured_content).framework == "sphinx"
        (repo / "docs" / "conf.py").unlink()
        result = await session.call_tool("list_doc_files", {"repo": str(repo)})
        assert ListDocFilesOutput.model_validate(result.structured_content).framework == "none"


@pytest.mark.parametrize(
    "path",
    [
        "../outside",
        "docs/../README.md",
        "/etc/passwd",
        "C:/Windows/win.ini",
        "docs\\file.md",
        ".git/config",
        ".git",
        ".GIT/config",
        "missing.txt",
    ],
)
async def test_path_rejections(repo: Path, path: str) -> None:
    async with connected(repo) as session:
        result = await session.call_tool("read_file", {"repo": str(repo), "file_path": path})
        code = "FILE_NOT_FOUND" if path == "missing.txt" else "PATH_OUTSIDE_REPO"
        assert code in error_text(result)


async def test_repo_rejections(repo: Path, tmp_path: Path) -> None:
    not_git = tmp_path / "not-git"
    not_git.mkdir()
    async with connected(repo) as session:
        for path, code in [
            (tmp_path / "missing", "REPO_NOT_FOUND"),
            (not_git, "NOT_A_GIT_REPO"),
            (repo / "docs", "NOT_A_GIT_REPO"),
            (repo / ".git", "PATH_OUTSIDE_REPO"),
        ]:
            result = await session.call_tool("list_doc_files", {"repo": str(path)})
            assert code in error_text(result)
    async with connected(repo, Config(allowed_roots=(not_git,))) as session:
        result = await session.call_tool("list_doc_files", {"repo": str(repo)})
        assert "PATH_OUTSIDE_REPO" in error_text(result)


async def test_symlinks_cannot_escape_or_read_git(repo: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("outside")
    (repo / "escape.md").symlink_to(outside)
    (repo / "metadata.md").symlink_to(repo / ".git" / "config")
    (repo / "hidden").symlink_to(repo / ".git", target_is_directory=True)
    async with connected(repo) as session:
        for path in ["escape.md", "metadata.md", "hidden/config"]:
            result = await session.call_tool("read_file", {"repo": str(repo), "file_path": path})
            assert "PATH_OUTSIDE_REPO" in error_text(result)
        result = await session.call_tool("list_doc_files", {"repo": str(repo)})
        assert "PATH_OUTSIDE_REPO" in error_text(result)
    alias = tmp_path / "alias"
    alias.symlink_to(repo, target_is_directory=True)
    with pytest.raises(ToolError, match="PATH_OUTSIDE_REPO"):
        Sandbox(alias, allowed_roots=(tmp_path / "elsewhere",))
    assert Sandbox(alias, allowed_roots=(tmp_path,)).root == repo


async def test_resources_defaults_overrides_and_language_selection(repo: Path) -> None:
    async with connected(repo) as session:
        assert str((await session.list_resources()).resources[0].uri) == "docter://style-guide"
        templates = (await session.list_resource_templates()).resource_templates
        assert templates[0].uri_template == "docter://glossary/{lang}"
        assert await resource_text(session, "docter://style-guide") == Config.DEFAULT_STYLE_GUIDE
        settings = repo / ".docter"
        settings.mkdir()
        (settings / "style.md").write_text("# Project rules\n")
        (settings / "glossary.yaml").write_text("de:\n  client: Kunde\nfr:\n  client: client\n")
        assert await resource_text(session, "docter://style-guide") == "# Project rules\n"
        assert yaml.safe_load(await resource_text(session, "docter://glossary/de")) == {
            "client": "Kunde"
        }
        with pytest.raises(MCPError, match="FILE_NOT_FOUND"):
            await resource_text(session, "docter://glossary/es")


async def test_resources_do_not_follow_tool_repo(repo: Path, tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    await anyio.run_process(["git", "init", "-q", str(other)])
    (other / ".docter").mkdir()
    (other / ".docter" / "style.md").write_text("other")
    async with connected(repo) as session:
        await session.call_tool("list_doc_files", {"repo": str(other)})
        assert await resource_text(session, "docter://style-guide") == Config.DEFAULT_STYLE_GUIDE


async def test_resources_reject_outside_roots(repo: Path, tmp_path: Path) -> None:
    async with connected(repo, Config(allowed_roots=(tmp_path / "elsewhere",))) as session:
        with pytest.raises(MCPError, match="PATH_OUTSIDE_REPO"):
            await resource_text(session, "docter://style-guide")


@pytest.mark.parametrize("text", ["[", "[]", "de: []", "de:\n  client: 42"])
async def test_malformed_glossaries_report_errors(repo: Path, text: str) -> None:
    (repo / ".docter").mkdir()
    (repo / ".docter" / "glossary.yaml").write_text(text)
    async with connected(repo) as session:
        with pytest.raises(MCPError):
            await resource_text(session, "docter://glossary/de")


async def test_missing_glossary_and_resource_symlink(repo: Path, tmp_path: Path) -> None:
    async with connected(repo) as session:
        with pytest.raises(MCPError, match="FILE_NOT_FOUND"):
            await resource_text(session, "docter://glossary/de")
        (repo / ".docter").symlink_to(tmp_path, target_is_directory=True)
        with pytest.raises(MCPError, match="PATH_OUTSIDE_REPO"):
            await resource_text(session, "docter://style-guide")


def test_git_environment_cannot_redirect_validation(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_DIR", str(repo / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path))
    with pytest.raises(ToolError, match="NOT_A_GIT_REPO"):
        Sandbox(tmp_path)
    assert Sandbox(repo).root == repo


def test_safe_reads_do_not_write(repo: Path) -> None:
    sandbox = Sandbox(repo)
    before = {p: p.read_bytes() for p in repo.rglob("*") if p.is_file()}
    sandbox.read_text("README.md")
    list(sandbox.files())
    assert before == {p: p.read_bytes() for p in repo.rglob("*") if p.is_file()}


async def test_stdio_console_script(repo: Path) -> None:
    command = shutil.which("docter-mcp")
    assert command is not None
    params = StdioServerParameters(
        command=command,
        args=["--repo", str(repo)],
        env={"DOCTER_ALLOWED_ROOTS": str(repo)},
    )
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write, read_timeout_seconds=10) as session,
    ):
        await session.initialize()
        result = await session.call_tool("get_server_info", {})
        assert not result.is_error
        assert await resource_text(session, "docter://style-guide") == Config.DEFAULT_STYLE_GUIDE


async def test_invalid_encoding_is_not_a_successful_empty_read(repo: Path) -> None:
    (repo / "binary.txt").write_bytes(b"\xff")
    (repo / "empty.txt").write_bytes(b"")
    async with connected(repo) as session:
        result = await session.call_tool(
            "read_file", {"repo": str(repo), "file_path": "binary.txt"}
        )
        assert "Cannot read UTF-8" in error_text(result)
        result = await session.call_tool("read_file", {"repo": str(repo), "file_path": "empty.txt"})
        assert ReadFileOutput.model_validate(result.structured_content).total_lines == 0


async def test_style_directory_is_an_error_not_the_default(repo: Path) -> None:
    (repo / ".docter" / "style.md").mkdir(parents=True)
    async with connected(repo) as session:
        with pytest.raises(MCPError, match="FILE_NOT_FOUND"):
            await resource_text(session, "docter://style-guide")


async def test_non_regular_files_are_rejected(repo: Path) -> None:
    os.mkfifo(repo / "pipe.txt")
    async with connected(repo) as session:
        result = await session.call_tool("read_file", {"repo": str(repo), "file_path": "pipe.txt"})
        assert "FILE_NOT_FOUND" in error_text(result)
