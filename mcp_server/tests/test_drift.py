"""Fresh-source drift checks and opt-in subprocess example execution."""

import time
from dataclasses import replace
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from test_foundation import connected, error_text

from docter_contract import (
    CheckDocDriftInput,
    CheckDocDriftOutput,
    DriftType,
    GetServerInfoOutput,
    IndexRepoInput,
    RunDocExamplesInput,
    RunDocExamplesOutput,
)
from docter_mcp.analysis import examples
from docter_mcp.analysis.drift import check_doc_drift
from docter_mcp.analysis.examples import run_doc_examples
from docter_mcp.config import Config
from docter_mcp.indexing.service import Embedder, index_repo


@pytest.fixture
async def indexed_config(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    def documents(self: Embedder, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(Embedder, "documents", documents)
    config = Config(index_dir=tmp_path / "index")
    await index_repo(IndexRepoInput(repo=str(repo)), config)
    return config


def test_sample_repo_exact_issues(
    repo: Path, indexed_config: Config, caplog: pytest.LogCaptureFixture
) -> None:
    request = CheckDocDriftInput(repo=str(repo))
    before = {p: p.read_bytes() for p in repo.rglob("*") if p.is_file()}
    output = check_doc_drift(request, indexed_config)
    assert [(i.file, i.line, i.symbol, i.type.value) for i in output.issues] == [
        ("docs/index.md", 5, None, "stale_reference"),
        ("docs/index.md", 6, None, "broken_link"),
        ("src/mylib/client.py", 98, "mylib.client.Client.from_url", "missing_param_doc"),
        ("src/mylib/client.py", 98, "mylib.client.Client.from_url", "param_mismatch"),
        ("src/mylib/client.py", 98, "mylib.client.Client.from_url", "stale_param_doc"),
        ("src/mylib/client.py", 121, "mylib.client.connect", "missing_param_doc"),
        ("src/mylib/client.py", 121, "mylib.client.connect", "param_mismatch"),
    ]
    assert "'mylib.client.OldClient'" in output.issues[0].detail
    assert "'url'" in output.issues[2].detail
    assert "'address'" in output.issues[4].detail
    assert "'secure'" in output.issues[5].detail
    assert "broken.py:4: Skipped: invalid Python syntax" in caplog.text
    assert check_doc_drift(request, indexed_config) == output
    assert before == {p: p.read_bytes() for p in repo.rglob("*") if p.is_file()}


@pytest.mark.parametrize(
    "doc",
    [
        "Args:\n        value: Input.\n        *args: More.\n        **kwargs: Options.",
        "Parameters\n    ----------\n    value : int\n        Input.\n"
        "    *args : int\n        More.\n    **kwargs : int\n        Options.",
        ":param value: Input.\n    :param args: More.\n    :param kwargs: Options.",
    ],
)
def test_parameters_in_all_styles_and_private_symbols(
    repo: Path, indexed_config: Config, doc: str
) -> None:
    (repo / "params.py").write_text(
        '"""Helpers."""\n'
        "def complete(self, cls, value, /, *args, **kwargs):\n"
        f'    """Complete.\n\n    {doc}\n    """\n'
        "    pass\n"
        "def _private(current):\n"
        '    """Changed.\n\n    :param previous: Old.\n    """\n'
        "    pass\n"
        "def undocumented(value):\n"
        "    pass\n"
    )
    output = check_doc_drift(
        CheckDocDriftInput(repo=str(repo), path_prefix="params.py"), indexed_config
    )
    assert {i.type for i in output.issues} == {
        DriftType.PARAM_MISMATCH,
        DriftType.MISSING_PARAM_DOC,
        DriftType.STALE_PARAM_DOC,
    }
    assert all(i.symbol == "params._private" for i in output.issues)


def test_prefix_and_index_wide_references_are_fresh(repo: Path, indexed_config: Config) -> None:
    page = repo / "docs" / "fresh.md"
    page.write_text(
        "`mylib.client.Client.send` exists.\n"
        "`mylib.client.Deleted` does not.\n"
        "``mylib.client.AlsoDeleted()`` does not.\n"
        "`value` and `docs/index.md` are not symbol references.\n"
        "[Existing](index.md#mylib-documentation)\n"
        "```python\n"
        "`mylib.client.NotProse`\n"
        "[not a link](absent.md)\n"
        "```\n"
    )
    request = CheckDocDriftInput(repo=str(repo), path_prefix="docs/fresh")
    assert [(i.line, i.type) for i in check_doc_drift(request, indexed_config).issues] == [
        (2, DriftType.STALE_REFERENCE),
        (3, DriftType.STALE_REFERENCE),
    ]
    page.write_text("`mylib.client.Client` is current.\n")
    assert check_doc_drift(request, indexed_config).issues == []


def test_docstring_symbol_references(repo: Path, indexed_config: Config) -> None:
    source = repo / "src" / "mylib" / "legacy.py"
    source.write_text(
        '"""See `mylib.client.Client`."""\ndef helper():\n'
        '    """See ``mylib.client.Removed``.\n\n'
        '    Again ``mylib.client.Removed``.\n    """\n'
    )
    output = check_doc_drift(
        CheckDocDriftInput(repo=str(repo), path_prefix="src/mylib/legacy.py"), indexed_config
    )
    assert [(i.line, i.symbol, i.type) for i in output.issues] == [
        (3, "mylib.legacy.helper", DriftType.STALE_REFERENCE),
        (5, "mylib.legacy.helper", DriftType.STALE_REFERENCE),
    ]


def test_markdown_links_anchors_and_reference_links(repo: Path, indexed_config: Config) -> None:
    (repo / "docs" / "target.md").write_text(
        "# Hello, *World*!\n"
        "# Hello, *World*!\n"
        "Setext title\n"
        "============\n"
        '<a id="explicit"></a>\n'
        "## Custom {#custom-id}\n"
        "## [Linked title](https://example.invalid) &amp; text\n"
        "```md\n"
        "# Not a heading\n"
        "```\n"
    )
    (repo / "docs" / "check.md").write_text(
        "# Local\n"
        "[one](target.md#hello-world)\n"
        "[two](target.md#hello-world-1)\n"
        "[setext](target.md#setext-title)\n"
        "[explicit](target.md#explicit)\n"
        "[custom](target.md#custom-id)\n"
        "[parent](../README.md#mylib)\n"
        "[self](#local)\n"
        "[query](target.md?version=1#hello-world)\n"
        "[web](https://example.invalid/missing#anchor)\n"
        "[email](mailto:docs@example.invalid)\n"
        "![image](missing.png)\n"
        "[missing](target.md#absent)\n"
        "[code](target.md#not-a-heading)\n"
        "[valid][known]\n"
        "[known]: target.md#hello-world\n"
        "[broken][unknown]\n"
        "[gone]: missing.md\n"
        "`[not prose](not-prose.md)`\n"
        "[linked heading](target.md#linked-title--text)\n"
        "[overridden heading](target.md#custom)\n"
    )
    output = check_doc_drift(
        CheckDocDriftInput(repo=str(repo), path_prefix="docs/check.md"), indexed_config
    )
    assert [(i.line, i.type) for i in output.issues] == [
        (12, DriftType.BROKEN_LINK),
        (13, DriftType.BROKEN_LINK),
        (14, DriftType.BROKEN_LINK),
        (17, DriftType.BROKEN_LINK),
        (18, DriftType.BROKEN_LINK),
        (21, DriftType.BROKEN_LINK),
    ]


def test_rst_links_labels_and_headings(repo: Path, indexed_config: Config) -> None:
    (repo / "docs" / "target.rst").write_text(
        ".. _explicit-label:\n\nTitle here\n==========\n\n"
        ".. code-block:: python\n\n    # Not a heading\n"
    )
    (repo / "docs" / "check.rst").write_text(
        "`Heading <target.rst#title-here>`_\n"
        "`Label <target.rst#explicit-label>`_\n"
        "`Missing <target.rst#absent>`_\n"
        "`Missing file <missing.rst>`_\n"
        ".. _guide: target.rst#explicit-label\n"
        ".. _gone: gone.rst\n"
        ".. code-block:: rst\n\n"
        "    `Ignored <ignored.rst>`_\n"
    )
    output = check_doc_drift(
        CheckDocDriftInput(repo=str(repo), path_prefix="docs/check.rst"), indexed_config
    )
    assert [(i.line, i.type) for i in output.issues] == [
        (3, DriftType.BROKEN_LINK),
        (4, DriftType.BROKEN_LINK),
        (6, DriftType.BROKEN_LINK),
    ]


def test_unsafe_links_are_issues_without_reading_targets(
    repo: Path, indexed_config: Config, tmp_path: Path
) -> None:
    (repo / "docs" / "escape.md").symlink_to(tmp_path / "outside.md")
    (repo / "docs" / "check.md").write_text(
        "[traversal](../../outside.md)\n"
        "[metadata](../.git/config)\n"
        "[encoded](../%2egit/config)\n"
        "[symlink](escape.md)\n"
    )
    output = check_doc_drift(
        CheckDocDriftInput(repo=str(repo), path_prefix="docs/check.md"), indexed_config
    )
    assert len(output.issues) == 4
    assert all(i.type is DriftType.BROKEN_LINK and "boundary" in i.detail for i in output.issues)


def test_drift_requires_an_index(repo: Path, tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="NOT_INDEXED"):
        check_doc_drift(CheckDocDriftInput(repo=str(repo)), Config(index_dir=tmp_path / "missing"))


@pytest.mark.parametrize("prefix", ["../", ".git/", "docs\\", "/outside"])
def test_drift_rejects_unsafe_prefixes(repo: Path, indexed_config: Config, prefix: str) -> None:
    with pytest.raises(ToolError, match="PATH_OUTSIDE_REPO"):
        check_doc_drift(CheckDocDriftInput(repo=str(repo), path_prefix=prefix), indexed_config)


def test_ignore_rules_and_encoding_failures(repo: Path, indexed_config: Config) -> None:
    (repo / ".gitignore").write_text("ignored.py\nignored.md\n")
    (repo / "ignored.py").write_bytes(b"\xff")
    (repo / "ignored.md").write_bytes(b"\xff")
    (repo / ".git" / "hidden.md").write_bytes(b"\xff")
    request = CheckDocDriftInput(repo=str(repo))
    check_doc_drift(request, indexed_config)
    (repo / "bad.md").write_bytes(b"\xff")
    with pytest.raises(ToolError, match="Cannot read UTF-8"):
        check_doc_drift(request, indexed_config)


@pytest.mark.parametrize("flag", ["true", "1", "TRUE", " true "])
def test_execution_environment_opt_in(flag: str) -> None:
    assert Config.from_env({"DOCSMITH_ALLOW_EXEC": flag}).allow_exec
    assert not Config.from_env(
        {"DOCSMITH_ALLOW_EXEC": "false", "DOCTER_ALLOW_EXEC": "true"}
    ).allow_exec
    with pytest.raises(ValueError, match="DOCSMITH_ALLOW_EXEC"):
        Config.from_env({"DOCSMITH_ALLOW_EXEC": "yes"})


def test_examples_disabled_before_any_file_access(repo: Path, tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="EXECUTION_DISABLED"):
        run_doc_examples(RunDocExamplesInput(repo=str(tmp_path / "missing")), Config.from_env({}))
    (repo / "example.md").write_text("```python runnable\nraise RuntimeError('never run')\n```\n")
    with pytest.raises(ToolError, match="EXECUTION_DISABLED"):
        run_doc_examples(RunDocExamplesInput(repo=str(repo)), Config())


def test_markdown_examples_output_isolation_and_read_only(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = repo / "examples.md"
    path.write_text(
        "```python\nraise RuntimeError('unmarked')\n```\n"
        "```python runnable\n"
        "import sys\n"
        "from pathlib import Path\n"
        "print(Path.cwd().name)\n"
        "print('stderr output', file=sys.stderr)\n"
        "value = 42\n"
        "```\n"
        "~~~{.python .runnable}\n"
        "assert 'value' not in globals()\n"
        "from mylib.client import Client\n"
        "print(Client('local').address)\n"
        "~~~\n"
        "```python runnable\n"
        "print('before failure')\n"
        "raise RuntimeError('broken example')\n"
        "```\n"
    )
    before = {p: p.read_bytes() for p in repo.rglob("*") if p.is_file()}
    output = run_doc_examples(
        RunDocExamplesInput(repo=str(repo), file_path="examples.md"), Config(allow_exec=True)
    )
    assert [(r.line, r.passed) for r in output.results] == [(5, True), (12, True), (17, False)]
    assert output.results[0].output == "repo\nstderr output\n"
    assert output.results[1].output == "local:8080\n"
    assert "before failure" in output.results[2].output
    assert "RuntimeError: broken example" in output.results[2].output
    assert 'File "examples.md", line 18' in output.results[2].output
    assert capsys.readouterr().out == ""
    assert before == {p: p.read_bytes() for p in repo.rglob("*") if p.is_file()}


def test_doctests_import_module_preserve_state_and_compare_outputs(repo: Path) -> None:
    (repo / "src" / "mylib" / "examples.py").write_text(
        '"""Examples.\n\n>>> twice(3)\n6\n"""\n'
        "def twice(value):\n"
        '    """Double.\n\n'
        "    >>> value = twice(2)\n"
        "    >>> value + 1\n"
        "    5\n"
        '    """\n'
        "    return value * 2\n"
        "\n"
        "class Example:\n"
        '    """A class.\n\n    >>> twice(2)\n    999\n    """\n'
    )
    output = run_doc_examples(
        RunDocExamplesInput(repo=str(repo), file_path="src/mylib/examples.py"),
        Config(allow_exec=True),
    )
    assert [(r.line, r.passed) for r in output.results] == [(3, True), (9, True), (18, False)]
    assert "Expected:" in output.results[2].output
    assert "Got:" in output.results[2].output
    assert "999" in output.results[2].output
    assert "line 18" in output.results[2].output


def test_doctest_import_error_is_a_failed_example(repo: Path) -> None:
    (repo / "error.py").write_text('""">>> 1\n1\n"""\nraise RuntimeError("import failed")\n')
    output = run_doc_examples(
        RunDocExamplesInput(repo=str(repo), file_path="error.py"), Config(allow_exec=True)
    )
    assert not output.results[0].passed
    assert "RuntimeError: import failed" in output.results[0].output


def test_unterminated_runnable_fence_extends_to_eof(repo: Path) -> None:
    (repo / "unclosed.md").write_text("```python runnable\nprint('at EOF')\n")
    output = run_doc_examples(
        RunDocExamplesInput(repo=str(repo), file_path="unclosed.md"), Config(allow_exec=True)
    )
    assert [(r.line, r.passed, r.output) for r in output.results] == [(2, True, "at EOF\n")]


def test_actual_example_timeout_captures_output(repo: Path) -> None:
    assert Config.EXAMPLE_TIMEOUT == 20
    (repo / "slow.md").write_text(
        "```python runnable\nimport time\nprint('started', flush=True)\ntime.sleep(30)\n```\n"
    )
    started = time.monotonic()
    output = run_doc_examples(
        RunDocExamplesInput(repo=str(repo), file_path="slow.md"), Config(allow_exec=True)
    )
    assert 20 <= time.monotonic() - started < 26
    assert not output.results[0].passed
    assert "started" in output.results[0].output
    assert "timed out after 20s" in output.results[0].output


def test_timeout_also_terminates_descendants_holding_output_pipes(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Config, "EXAMPLE_TIMEOUT", 1)
    (repo / "children.md").write_text(
        "```python runnable\n"
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        "print('spawned child', flush=True)\n"
        "time.sleep(30)\n"
        "```\n"
    )
    started = time.monotonic()
    output = run_doc_examples(
        RunDocExamplesInput(repo=str(repo), file_path="children.md"), Config(allow_exec=True)
    )
    assert time.monotonic() - started < 5
    assert not output.results[0].passed
    assert "spawned child" in output.results[0].output
    assert "timed out after 1s" in output.results[0].output


def test_example_discovery_respects_ignores_and_sandbox(repo: Path, tmp_path: Path) -> None:
    (repo / ".gitignore").write_text("ignored.md\n")
    (repo / "ignored.md").write_bytes(b"\xff")
    (repo / ".git" / "hidden.md").write_bytes(b"\xff")
    assert (
        run_doc_examples(RunDocExamplesInput(repo=str(repo)), Config(allow_exec=True)).results == []
    )
    outside = tmp_path / "outside.md"
    outside.write_text("```python runnable\nraise RuntimeError('never run')\n```\n")
    (repo / "escape.md").symlink_to(outside)
    for path in ["../outside.md", ".git/config", "escape.md"]:
        with pytest.raises(ToolError, match="PATH_OUTSIDE_REPO"):
            run_doc_examples(
                RunDocExamplesInput(repo=str(repo), file_path=path), Config(allow_exec=True)
            )
    with pytest.raises(ToolError, match="FILE_NOT_FOUND"):
        run_doc_examples(
            RunDocExamplesInput(repo=str(repo), file_path="absent.md"), Config(allow_exec=True)
        )


def test_examples_validate_all_sources_before_execution(repo: Path) -> None:
    (repo / "a.md").write_text(
        "```python runnable\nraise RuntimeError('must not execute yet')\n```\n"
    )
    (repo / "z.py").write_text('"""\n    >>> 1\n  invalid indentation\n"""\n')
    with pytest.raises(ToolError, match="Cannot parse doctest"):
        run_doc_examples(RunDocExamplesInput(repo=str(repo)), Config(allow_exec=True))


def test_drift_only_executes_when_enabled_and_reuses_runner(
    repo: Path, indexed_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    (repo / "examples.md").write_text("```python runnable\nassert 1 == 2\n```\n")
    request = CheckDocDriftInput(repo=str(repo), path_prefix="examples.md")
    calls = []
    original = examples.run_doc_examples

    def tracked(request: RunDocExamplesInput, config: Config) -> RunDocExamplesOutput:
        calls.append(request.file_path)
        return original(request, config)

    monkeypatch.setattr(examples, "run_doc_examples", tracked)
    assert check_doc_drift(request, indexed_config).issues == []
    assert calls == []
    output = check_doc_drift(request, replace(indexed_config, allow_exec=True))
    assert calls == ["examples.md"]
    assert len(output.issues) == 1
    assert output.issues[0].type is DriftType.FAILING_EXAMPLE
    assert output.issues[0].file == "examples.md"
    assert output.issues[0].line == 2
    assert "AssertionError" in output.issues[0].detail


async def test_mcp_analysis_contracts_errors_and_risk_description(
    repo: Path, indexed_config: Config
) -> None:
    async with connected(repo, indexed_config) as session:
        tools = (await session.list_tools()).tools
        description = next(t.description for t in tools if t.name == "run_doc_examples")
        assert description is not None
        assert "arbitrary repository code" in description
        assert "20s timeout" in description
        result = await session.call_tool("check_doc_drift", {"repo": str(repo)})
        assert not result.is_error
        assert CheckDocDriftOutput.model_validate(result.structured_content) == check_doc_drift(
            CheckDocDriftInput(repo=str(repo)), indexed_config
        )
        result = await session.call_tool("run_doc_examples", {"repo": str(repo)})
        assert "EXECUTION_DISABLED" in error_text(result)
        result = await session.call_tool("get_server_info", {})
        assert not GetServerInfoOutput.model_validate(
            result.structured_content
        ).capabilities.execution_enabled
    (repo / "runnable.md").write_text("```python runnable\nprint('ok')\n```\n")
    async with connected(repo, replace(indexed_config, allow_exec=True)) as session:
        result = await session.call_tool(
            "run_doc_examples", {"repo": str(repo), "file_path": "runnable.md"}
        )
        assert not result.is_error
        output = RunDocExamplesOutput.model_validate(result.structured_content)
        assert [(r.passed, r.output) for r in output.results] == [(True, "ok\n")]
