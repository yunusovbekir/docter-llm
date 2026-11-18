"""Exact documentation gaps and coverage, including fresh-source MCP integration."""

import subprocess
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from test_foundation import connected, error_text

from docter_contract import DocGap, FindDocGapsInput, FindDocGapsOutput
from docter_mcp.analysis.gaps import find_doc_gaps
from docter_mcp.config import Config


@pytest.fixture
def empty_repo(tmp_path: Path) -> Path:
    root = tmp_path / "empty"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def rows(output: FindDocGapsOutput) -> list[tuple[str, int, str, str, list[str]]]:
    return [
        (item.file, item.line, item.qualname, item.kind.value, [gap.value for gap in item.missing])
        for item in output.items
    ]


def test_sample_repo_exact_gaps(repo: Path, caplog: pytest.LogCaptureFixture) -> None:
    output = find_doc_gaps(FindDocGapsInput(repo=str(repo)), Config())
    client = "src/mylib/client.py"
    legacy = "src/mylib/legacy.py"
    assert output.total_public == 18
    assert output.documented == 14
    assert output.coverage_percent == pytest.approx(100 * 14 / 18)
    assert rows(output) == [
        (client, 1, "mylib.client", "module", ["reference_page"]),
        (client, 11, "mylib.client.ClientError", "class", ["reference_page"]),
        (
            client,
            23,
            "mylib.client.Client.__init__",
            "method",
            ["docstring", "params", "reference_page"],
        ),
        (client, 28, "mylib.client.Client.name", "property", ["returns", "reference_page"]),
        (client, 39, "mylib.client.Client.address", "property", ["returns", "reference_page"]),
        (client, 44, "mylib.client.Client.send", "method", ["reference_page"]),
        (client, 76, "mylib.client.Client.fetch", "method", ["reference_page"]),
        (
            client,
            93,
            "mylib.client.Client.parse_url",
            "method",
            ["docstring", "params", "returns", "reference_page"],
        ),
        (
            client,
            98,
            "mylib.client.Client.from_url",
            "method",
            ["params", "returns", "reference_page"],
        ),
        (client, 113, "mylib.client.Client.Options", "class", ["reference_page"]),
        (
            client,
            116,
            "mylib.client.Client.Options.as_dict",
            "method",
            ["returns", "reference_page"],
        ),
        (client, 121, "mylib.client.connect", "function", ["params", "reference_page"]),
        (client, 134, "mylib.client.ping", "function", ["docstring", "params", "reference_page"]),
        (client, 142, "mylib.client.Undocumented", "class", ["docstring", "reference_page"]),
        (legacy, 1, "mylib.legacy", "module", ["reference_page"]),
        (legacy, 4, "mylib.legacy.mean", "function", ["reference_page"]),
    ]
    assert "src/mylib/broken.py:4: Skipped: invalid Python syntax" in caplog.text
    assert find_doc_gaps(FindDocGapsInput(repo=str(repo)), Config()) == output


def test_include_private_keeps_public_coverage(repo: Path) -> None:
    public = find_doc_gaps(FindDocGapsInput(repo=str(repo)), Config())
    private = find_doc_gaps(FindDocGapsInput(repo=str(repo), include_private=True), Config())
    assert private.model_dump(exclude={"items"}) == public.model_dump(exclude={"items"})
    assert rows(private) == sorted(
        [
            *rows(public),
            (
                "src/mylib/client.py",
                107,
                "mylib.client.Client._write",
                "method",
                ["docstring", "params", "returns"],
            ),
        ]
    )


@pytest.mark.parametrize(
    ("prefix", "total", "documented"),
    [
        ("src/mylib/client.py", 15, 11),
        ("src/mylib/client", 15, 11),
        ("src/mylib/", 18, 14),
        ("src/mylib/legacy.py", 2, 2),
        ("src/mylib/_internal.py", 0, 0),
        ("absent/", 0, 0),
    ],
)
def test_path_prefix_scopes_symbols_not_doc_search(
    repo: Path, prefix: str, total: int, documented: int
) -> None:
    public = find_doc_gaps(FindDocGapsInput(repo=str(repo)), Config())
    output = find_doc_gaps(FindDocGapsInput(repo=str(repo), path_prefix=prefix), Config())
    assert output.total_public == total
    assert output.documented == documented
    assert output.coverage_percent == pytest.approx(100 * documented / total if total else 100)
    assert output.items == [item for item in public.items if item.file.startswith(prefix)]


@pytest.mark.parametrize(
    "docstring",
    [
        """Complete.

    Args:
        value: Input.
        *args: More inputs.
        flag: A flag.
        **kwargs: Options.

    Returns:
        The result.

    Raises:
        ValueError: Invalid input.
    """,
        """Complete.

    Parameters
    ----------
    value : int
        Input.
    *args : int
        More inputs.
    flag : bool
        A flag.
    **kwargs : int
        Options.

    Returns
    -------
    int
        The result.

    Raises
    ------
    ValueError
        Invalid input.
    """,
        """Complete.

    :param value: Input.
    :param args: More inputs.
    :param flag: A flag.
    :param kwargs: Options.
    :returns: The result.
    :raises ValueError: Invalid input.
    """,
    ],
)
def test_sections_in_each_style_and_signature_parameter_kinds(
    empty_repo: Path, docstring: str
) -> None:
    (empty_repo / "example.py").write_text(
        '"""Helpers."""\n'
        "def complete(self, cls, value, /, *args, flag=False, **kwargs) -> int:\n"
        f'    """{docstring}"""\n'
        "    if not value:\n"
        "        raise ValueError('invalid')\n"
        "    return value\n"
    )
    assert find_doc_gaps(FindDocGapsInput(repo=str(empty_repo)), Config()) == FindDocGapsOutput(
        total_public=2, documented=2, coverage_percent=100, items=[]
    )


def test_all_applicable_gaps_and_empty_docstrings(empty_repo: Path) -> None:
    (empty_repo / "example.py").write_text(
        '"""   """\n'
        "def missing(self, cls, value, /, *args, flag=False, **kwargs) -> int:\n"
        "    raise ValueError('invalid')\n"
        "\n"
        "def partial(value: int) -> int:\n"
        '    """Summary."""\n'
        "    raise TypeError('invalid')\n"
    )
    output = find_doc_gaps(FindDocGapsInput(repo=str(empty_repo)), Config())
    assert rows(output) == [
        ("example.py", 1, "example", "module", ["docstring"]),
        (
            "example.py",
            2,
            "example.missing",
            "function",
            ["docstring", "params", "returns", "raises"],
        ),
        ("example.py", 5, "example.partial", "function", ["params", "returns", "raises"]),
    ]
    assert output.total_public == 3
    assert output.documented == 1
    assert output.coverage_percent == pytest.approx(100 / 3)


@pytest.mark.parametrize(
    ("annotation", "missing"),
    [
        ("", []),
        (" -> None", []),
        (" -> 'None'", []),
        (' -> "None"', []),
        (" -> NoneType", []),
        (" -> type(None)", []),
        (" -> int", [DocGap.RETURNS]),
        (" -> int | None", [DocGap.RETURNS]),
        (" -> 'int'", [DocGap.RETURNS]),
    ],
)
def test_return_annotation_requirement(
    empty_repo: Path, annotation: str, missing: list[DocGap]
) -> None:
    (empty_repo / "example.py").write_text(
        f'"""Helpers."""\ndef value(){annotation}:\n    """A value."""\n    return None\n'
    )
    output = find_doc_gaps(FindDocGapsInput(repo=str(empty_repo)), Config())
    assert [item.missing for item in output.items] == ([missing] if missing else [])
    assert output.documented == 2
    assert output.coverage_percent == 100


def test_return_type_without_description_is_a_gap(empty_repo: Path) -> None:
    (empty_repo / "example.py").write_text(
        '"""Helpers."""\n'
        "def value() -> int:\n"
        '    """A value.\n\n    :rtype: int\n    """\n'
        "    return 1\n"
    )
    output = find_doc_gaps(FindDocGapsInput(repo=str(empty_repo)), Config())
    assert rows(output) == [
        ("example.py", 2, "example.value", "function", ["returns"]),
    ]


def test_only_direct_raises_require_a_section(empty_repo: Path) -> None:
    (empty_repo / "example.py").write_text(
        '"""Helpers."""\n'
        "def outer() -> None:\n"
        '    """Outer scope."""\n'
        "    def nested():\n"
        "        raise RuntimeError('nested')\n"
        "    try:\n"
        "        nested()\n"
        "    except RuntimeError as exc:\n"
        "        if str(exc):\n"
        "            raise exc\n"
        "        raise\n"
        "\n"
        "async def direct() -> None:\n"
        '    """Direct raise."""\n'
        "    raise ValueError\n"
    )
    output = find_doc_gaps(FindDocGapsInput(repo=str(empty_repo)), Config())
    assert rows(output) == [
        ("example.py", 13, "example.direct", "function", ["raises"]),
    ]


def test_reference_pages_require_docs_directory_and_exact_qualnames(empty_repo: Path) -> None:
    (empty_repo / "example.py").write_text(
        '"""Helpers."""\ndef run() -> None:\n    """Run it."""\n'
    )
    request = FindDocGapsInput(repo=str(empty_repo), path_prefix="example.py")
    assert find_doc_gaps(request, Config()).items == []
    docs = empty_repo / "docs"
    docs.mkdir()
    assert rows(find_doc_gaps(request, Config())) == [
        ("example.py", 1, "example", "module", ["reference_page"]),
        ("example.py", 2, "example.run", "function", ["reference_page"]),
    ]
    (docs / "page.MD").write_text("example.runner example.run_more other.example.run\n")
    assert len(find_doc_gaps(request, Config()).items) == 2
    (empty_repo / "README.md").write_text("# example\n")
    (docs / "reference.rst").write_text(".. autofunction:: example.run\n")
    assert find_doc_gaps(request, Config()).items == []
    (docs / "reference.rst").write_text("Call example.run.\n")
    assert find_doc_gaps(request, Config()).items == []


def test_empty_and_private_only_repos_have_full_public_coverage(empty_repo: Path) -> None:
    request = FindDocGapsInput(repo=str(empty_repo))
    expected = FindDocGapsOutput(total_public=0, documented=0, coverage_percent=100, items=[])
    assert find_doc_gaps(request, Config()) == expected
    (empty_repo / "_hidden.py").write_text("def helper():\n    pass\n")
    assert find_doc_gaps(request, Config()) == expected
    output = find_doc_gaps(request.model_copy(update={"include_private": True}), Config())
    assert rows(output) == [
        ("_hidden.py", 1, "_hidden", "module", ["docstring"]),
        ("_hidden.py", 1, "_hidden.helper", "function", ["docstring"]),
    ]
    assert output.coverage_percent == 100
    assert output.total_public == output.documented == 0


def test_ignores_git_metadata_and_gitignored_source_and_docs(empty_repo: Path) -> None:
    (empty_repo / "docs").mkdir()
    (empty_repo / ".gitignore").write_text("ignored.py\nignored.md\n")
    (empty_repo / "ignored.py").write_bytes(b"\xff")
    (empty_repo / "ignored.md").write_text("example\n")
    (empty_repo / ".git" / "secret.py").write_bytes(b"\xff")
    (empty_repo / "example.py").write_text('"""Helpers."""\n')
    output = find_doc_gaps(FindDocGapsInput(repo=str(empty_repo)), Config())
    assert output.total_public == output.documented == 1
    assert rows(output) == [
        ("example.py", 1, "example", "module", ["reference_page"]),
    ]


@pytest.mark.parametrize(
    "prefix", ["../", ".git/", "src/../../", "/tmp/", "C:/Windows/", "src\\mylib\\"]
)
def test_unsafe_path_prefixes_are_rejected(repo: Path, prefix: str) -> None:
    with pytest.raises(ToolError, match="PATH_OUTSIDE_REPO"):
        find_doc_gaps(FindDocGapsInput(repo=str(repo), path_prefix=prefix), Config())


@pytest.mark.parametrize("filename", ["escape.py", "docs/escape.md", "docs/escape.rst"])
def test_symlinked_files_cannot_escape(empty_repo: Path, tmp_path: Path, filename: str) -> None:
    outside = tmp_path / "outside"
    outside.write_text("outside")
    (empty_repo / "docs").mkdir()
    (empty_repo / filename).symlink_to(outside)
    with pytest.raises(ToolError, match="PATH_OUTSIDE_REPO"):
        find_doc_gaps(FindDocGapsInput(repo=str(empty_repo)), Config())


def test_docs_directory_and_prefix_cannot_escape(empty_repo: Path, tmp_path: Path) -> None:
    (empty_repo / "docs").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ToolError, match="PATH_OUTSIDE_REPO"):
        find_doc_gaps(FindDocGapsInput(repo=str(empty_repo)), Config())
    with pytest.raises(ToolError, match="PATH_OUTSIDE_REPO"):
        find_doc_gaps(FindDocGapsInput(repo=str(empty_repo), path_prefix="docs/"), Config())


@pytest.mark.parametrize("filename", ["example.py", "docs/page.md"])
def test_invalid_encoding_is_an_error(empty_repo: Path, filename: str) -> None:
    (empty_repo / "docs").mkdir()
    (empty_repo / filename).write_bytes(b"\xff")
    with pytest.raises(ToolError, match="Cannot read UTF-8"):
        find_doc_gaps(FindDocGapsInput(repo=str(empty_repo)), Config())


def test_analysis_is_read_only_fresh_and_needs_no_index(repo: Path, tmp_path: Path) -> None:
    config = Config(index_dir=tmp_path / "index")
    request = FindDocGapsInput(repo=str(repo), path_prefix="src/mylib/client.py")
    before = {path: path.read_bytes() for path in repo.rglob("*") if path.is_file()}
    first = find_doc_gaps(request, config)
    assert before == {path: path.read_bytes() for path in repo.rglob("*") if path.is_file()}
    assert not config.index_dir.exists()
    source = repo / "src" / "mylib" / "client.py"
    source.write_text(source.read_text().replace('"""The display name of the client."""', ""))
    second = find_doc_gaps(request, config)
    assert second.documented == first.documented - 1
    assert next(item for item in second.items if item.qualname.endswith(".name")).missing == [
        DocGap.DOCSTRING,
        DocGap.RETURNS,
        DocGap.REFERENCE_PAGE,
    ]


async def test_mcp_gap_tool_and_contract_errors(repo: Path, tmp_path: Path) -> None:
    async with connected(repo) as session:
        result = await session.call_tool(
            "find_doc_gaps",
            {"repo": str(repo), "include_private": True, "path_prefix": "src/mylib/client.py"},
        )
        assert not result.is_error
        assert FindDocGapsOutput.model_validate(result.structured_content) == find_doc_gaps(
            FindDocGapsInput(
                repo=str(repo), include_private=True, path_prefix="src/mylib/client.py"
            ),
            Config(),
        )
        for arguments, expected in [
            ({"repo": str(tmp_path / "missing")}, "REPO_NOT_FOUND"),
            ({"repo": str(repo / "docs")}, "NOT_A_GIT_REPO"),
            ({"repo": str(repo), "path_prefix": "../"}, "PATH_OUTSIDE_REPO"),
        ]:
            result = await session.call_tool("find_doc_gaps", arguments)
            assert expected in error_text(result)
    async with connected(repo, Config(allowed_roots=(tmp_path / "elsewhere",))) as session:
        result = await session.call_tool("find_doc_gaps", {"repo": str(repo)})
        assert "PATH_OUTSIDE_REPO" in error_text(result)
