from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from docter_contract import (
    CONTRACT_VERSION,
    TOOL_SPECS,
    CheckDocDriftInput,
    CheckDocDriftOutput,
    ContractModel,
    DocFormat,
    DocFramework,
    DocGap,
    DriftType,
    FindDocGapsInput,
    FindDocGapsOutput,
    GetServerInfoInput,
    GetServerInfoOutput,
    GetSymbolInput,
    GetSymbolOutput,
    IndexProgress,
    IndexRepoInput,
    IndexRepoOutput,
    ListDocFilesInput,
    ListDocFilesOutput,
    ParameterKind,
    ProposePatchInput,
    ProposePatchOutput,
    ReadFileInput,
    ReadFileOutput,
    RepoInput,
    RunDocExamplesInput,
    RunDocExamplesOutput,
    SearchCodeInput,
    SearchCodeOutput,
    SymbolKind,
    ToolName,
    TranslateFileInput,
    TranslateFileOutput,
)

REPO = "/home/user/project"

# One valid payload per model, as a client or server would send it over JSON.
VALID_PAYLOADS: dict[type[ContractModel], dict[str, Any]] = {
    GetServerInfoInput: {},
    GetServerInfoOutput: {
        "contract_version": "0.1.0",
        "server_version": "0.1.0",
        "capabilities": {
            "tools": ["index_repo", "search_code"],
            "resources": ["docter://style-guide"],
            "execution_enabled": False,
        },
    },
    IndexRepoInput: {"repo": REPO, "force": True},
    IndexRepoOutput: {"files_indexed": 12, "symbols": 80, "chunks": 95, "duration_ms": 1530},
    IndexProgress: {"progress": 3, "total": 12, "message": "Parsing src/lib/client.py"},
    SearchCodeInput: {"repo": REPO, "query": "retry with backoff", "k": 5, "kind": "function"},
    SearchCodeOutput: {
        "results": [
            {
                "file": "src/lib/retry.py",
                "qualname": "lib.retry.backoff",
                "kind": "function",
                "start_line": 10,
                "end_line": 24,
                "snippet": "def backoff(attempt: int) -> float: ...",
                "score": 0.82,
            },
            {
                "file": "src/lib/__init__.py",
                "kind": "module",
                "start_line": 1,
                "end_line": 1,
                "snippet": "from lib.retry import backoff",
                "score": 0.31,
            },
        ]
    },
    GetSymbolInput: {"repo": REPO, "qualname": "lib.client.Client.send"},
    GetSymbolOutput: {
        "qualname": "lib.client.Client.send",
        "kind": "method",
        "file": "src/lib/client.py",
        "start_line": 40,
        "end_line": 52,
        "signature": "def send(self, payload: bytes, *, timeout: float = 5.0) -> int",
        "parameters": [
            {"name": "self", "kind": "positional_or_keyword"},
            {"name": "payload", "annotation": "bytes", "kind": "positional_or_keyword"},
            {"name": "timeout", "annotation": "float", "default": "5.0", "kind": "keyword_only"},
        ],
        "returns": "int",
        "raises": ["TimeoutError"],
        "decorators": [],
        "docstring": "Send a payload.",
        "is_public": True,
        "source": "def send(self, payload, *, timeout=5.0):\n    ...",
    },
    ReadFileInput: {"repo": REPO, "file_path": "README.md", "start_line": 1, "end_line": 20},
    ReadFileOutput: {"file_path": "README.md", "content": "# lib\n", "total_lines": 1},
    ListDocFilesInput: {"repo": REPO},
    ListDocFilesOutput: {
        "framework": "mkdocs",
        "files": [
            {"path": "docs/index.md", "format": "markdown", "title": "Home"},
            {"path": "docs/api.rst", "format": "rst"},
        ],
    },
    FindDocGapsInput: {"repo": REPO, "include_private": True, "path_prefix": "src/lib/"},
    FindDocGapsOutput: {
        "coverage_percent": 75.0,
        "total_public": 4,
        "documented": 3,
        "items": [
            {
                "qualname": "lib.client.connect",
                "kind": "function",
                "file": "src/lib/client.py",
                "line": 7,
                "missing": ["docstring", "reference_page"],
            }
        ],
    },
    CheckDocDriftInput: {"repo": REPO, "path_prefix": "src/"},
    CheckDocDriftOutput: {
        "issues": [
            {
                "type": "stale_param_doc",
                "file": "src/lib/client.py",
                "line": 42,
                "symbol": "lib.client.Client.send",
                "detail": "Docstring documents 'retries', which is not a parameter.",
            },
            {"type": "broken_link", "file": "docs/index.md", "line": 3, "detail": "api.md#x"},
        ]
    },
    RunDocExamplesInput: {"repo": REPO, "file_path": "docs/index.md"},
    RunDocExamplesOutput: {
        "results": [{"file": "docs/index.md", "line": 10, "passed": False, "output": "NameError"}]
    },
    ProposePatchInput: {
        "repo": REPO,
        "file_path": "src/lib/client.py",
        "rationale": "Document the timeout parameter.",
        "edits": [{"old_str": "def send(", "new_str": "def send("}],
    },
    ProposePatchOutput: {
        "file_path": "src/lib/client.py",
        "patch": "--- a/src/lib/client.py\n+++ b/src/lib/client.py\n",
        "rationale": "Document the timeout parameter.",
        "is_new_file": False,
    },
    TranslateFileInput: {"repo": REPO, "file_path": "docs/index.md", "target_language": "pt-BR"},
    TranslateFileOutput: {
        "source_path": "docs/index.md",
        "target_path": "docs/pt-BR/index.md",
        "patch": "--- /dev/null\n+++ b/docs/pt-BR/index.md\n",
        "rationale": "Translate the home page.",
        "is_new_file": True,
        "warnings": ["Glossary has no entry for 'backoff'."],
    },
}


def test_contract_version() -> None:
    assert CONTRACT_VERSION == "0.2.0"


def test_every_tool_has_a_spec() -> None:
    assert set(TOOL_SPECS) == set(ToolName)


def test_every_registered_model_has_a_valid_payload() -> None:
    registered: set[type[BaseModel]] = set()
    for spec in TOOL_SPECS.values():
        registered |= {spec.input, spec.output}
        if spec.progress is not None:
            registered.add(spec.progress)
    assert registered == set(VALID_PAYLOADS)


@pytest.mark.parametrize("model", list(VALID_PAYLOADS), ids=lambda m: m.__name__)
def test_valid_payload_round_trips_through_json(model: type[ContractModel]) -> None:
    instance = model.model_validate(VALID_PAYLOADS[model])
    assert model.model_validate_json(instance.model_dump_json()) == instance


@pytest.mark.parametrize("model", list(VALID_PAYLOADS), ids=lambda m: m.__name__)
def test_unknown_fields_are_rejected(model: type[ContractModel]) -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        model.model_validate({**VALID_PAYLOADS[model], "unexpected": 1})


def test_models_are_immutable() -> None:
    instance = IndexRepoInput(repo=REPO)
    with pytest.raises(ValidationError, match="frozen_instance"):
        instance.force = True  # type: ignore[misc]


@pytest.mark.parametrize("name", [n for n in ToolName if n is not ToolName.GET_SERVER_INFO])
def test_repo_tool_inputs_require_repo(name: ToolName) -> None:
    model = TOOL_SPECS[name].input
    assert issubclass(model, RepoInput)
    assert "repo" in model.model_json_schema()["required"]
    payload = {k: v for k, v in VALID_PAYLOADS[model].items() if k != "repo"}
    with pytest.raises(ValidationError, match="repo"):
        model.model_validate(payload)


def test_get_server_info_takes_no_arguments() -> None:
    assert GetServerInfoInput.model_json_schema()["properties"] == {}


@pytest.mark.parametrize("repo", ["/home/user/project", "C:\\Users\\me\\project", "D:/src/lib"])
def test_repo_accepts_absolute_paths(repo: str) -> None:
    assert IndexRepoInput(repo=repo).repo == repo


@pytest.mark.parametrize("repo", ["", "project", "./project", "../project", "C:project"])
def test_repo_rejects_relative_paths(repo: str) -> None:
    with pytest.raises(ValidationError):
        IndexRepoInput(repo=repo)


def test_input_defaults() -> None:
    assert IndexRepoInput(repo=REPO).force is False
    search = SearchCodeInput(repo=REPO, query="parse")
    assert (search.k, search.kind) == (8, None)
    gaps = FindDocGapsInput(repo=REPO)
    assert (gaps.include_private, gaps.path_prefix) == (False, None)
    assert CheckDocDriftInput(repo=REPO).path_prefix is None
    assert RunDocExamplesInput(repo=REPO).file_path is None
    read = ReadFileInput(repo=REPO, file_path="a.py")
    assert (read.start_line, read.end_line) == (None, None)


@pytest.mark.parametrize("k", [0, 51])
def test_search_k_is_bounded(k: int) -> None:
    with pytest.raises(ValidationError, match="k"):
        SearchCodeInput(repo=REPO, query="parse", k=k)


def test_search_rejects_empty_query_and_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        SearchCodeInput(repo=REPO, query="")
    with pytest.raises(ValidationError):
        SearchCodeInput.model_validate({"repo": REPO, "query": "x", "kind": "variable"})


@pytest.mark.parametrize(("start", "end"), [(0, 5), (5, 4)])
def test_read_file_rejects_invalid_line_ranges(start: int, end: int) -> None:
    with pytest.raises(ValidationError):
        ReadFileInput(repo=REPO, file_path="a.py", start_line=start, end_line=end)


def test_read_file_accepts_single_line_and_open_ranges() -> None:
    ReadFileInput(repo=REPO, file_path="a.py", start_line=3, end_line=3)
    ReadFileInput(repo=REPO, file_path="a.py", start_line=3)
    ReadFileInput(repo=REPO, file_path="a.py", end_line=3)


def test_read_file_rejects_empty_path() -> None:
    with pytest.raises(ValidationError, match="file_path"):
        ReadFileInput(repo=REPO, file_path="")


def test_search_hit_and_symbol_reject_inverted_line_ranges() -> None:
    hit = VALID_PAYLOADS[SearchCodeOutput]["results"][0]
    with pytest.raises(ValidationError, match="end_line"):
        SearchCodeOutput.model_validate({"results": [{**hit, "start_line": 30}]})
    with pytest.raises(ValidationError, match="end_line"):
        GetSymbolOutput.model_validate({**VALID_PAYLOADS[GetSymbolOutput], "end_line": 1})


def test_get_symbol_parameters() -> None:
    symbol = GetSymbolOutput.model_validate(VALID_PAYLOADS[GetSymbolOutput])
    assert [p.kind for p in symbol.parameters] == [
        ParameterKind.POSITIONAL_OR_KEYWORD,
        ParameterKind.POSITIONAL_OR_KEYWORD,
        ParameterKind.KEYWORD_ONLY,
    ]
    assert symbol.parameters[0].annotation is None
    assert symbol.parameters[2].default == "5.0"


def test_index_progress() -> None:
    assert IndexProgress(progress=0).total is None
    with pytest.raises(ValidationError, match="progress must not exceed total"):
        IndexProgress(progress=13, total=12)
    with pytest.raises(ValidationError):
        IndexProgress(progress=-1)


def test_index_output_counts_are_non_negative() -> None:
    with pytest.raises(ValidationError, match="duration_ms"):
        IndexRepoOutput(files_indexed=0, symbols=0, chunks=0, duration_ms=-1)


def test_doc_gap_output_constraints() -> None:
    payload = VALID_PAYLOADS[FindDocGapsOutput]
    with pytest.raises(ValidationError, match="documented must not exceed total_public"):
        FindDocGapsOutput.model_validate({**payload, "documented": 5})
    with pytest.raises(ValidationError, match="coverage_percent"):
        FindDocGapsOutput.model_validate({**payload, "coverage_percent": 100.1})
    item = {**payload["items"][0], "missing": []}
    with pytest.raises(ValidationError, match="missing"):
        FindDocGapsOutput.model_validate({**payload, "items": [item]})


def test_find_doc_gaps_rejects_empty_path_prefix() -> None:
    with pytest.raises(ValidationError, match="path_prefix"):
        FindDocGapsInput(repo=REPO, path_prefix="")


def test_drift_issue_symbol_is_optional() -> None:
    output = CheckDocDriftOutput.model_validate(VALID_PAYLOADS[CheckDocDriftOutput])
    assert output.issues[1].symbol is None
    assert output.issues[1].type is DriftType.BROKEN_LINK


def test_propose_patch_with_new_content() -> None:
    patch = ProposePatchInput(
        repo=REPO, file_path="docs/new.md", rationale="Add a page.", new_content="# New\n"
    )
    assert patch.edits is None


def test_propose_patch_accepts_empty_new_content() -> None:
    assert ProposePatchInput(repo=REPO, file_path="a.md", rationale="Empty.", new_content="")


@pytest.mark.parametrize(
    "change",
    [
        {},
        {"edits": [{"old_str": "a", "new_str": "b"}], "new_content": "c"},
    ],
    ids=["neither", "both"],
)
def test_propose_patch_requires_exactly_one_change(change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="exactly one of edits or new_content"):
        ProposePatchInput.model_validate(
            {"repo": REPO, "file_path": "a.py", "rationale": "Fix.", **change}
        )


def test_propose_patch_rejects_empty_edits_and_old_str() -> None:
    base = {"repo": REPO, "file_path": "a.py", "rationale": "Fix."}
    with pytest.raises(ValidationError, match="edits"):
        ProposePatchInput.model_validate({**base, "edits": []})
    with pytest.raises(ValidationError, match="old_str"):
        ProposePatchInput.model_validate({**base, "edits": [{"old_str": "", "new_str": "x"}]})


@pytest.mark.parametrize("lang", ["de", "fil", "pt-BR", "zh-Hans", "sr-Latn-RS"])
def test_translate_accepts_language_tags(lang: str) -> None:
    assert TranslateFileInput(repo=REPO, file_path="a.md", target_language=lang)


@pytest.mark.parametrize("lang", ["", "German", "DE", "pt_BR", "e", "de-"])
def test_translate_rejects_invalid_language_tags(lang: str) -> None:
    with pytest.raises(ValidationError, match="target_language"):
        TranslateFileInput(repo=REPO, file_path="a.md", target_language=lang)


def test_list_doc_files_enums() -> None:
    output = ListDocFilesOutput.model_validate(VALID_PAYLOADS[ListDocFilesOutput])
    assert output.framework is DocFramework.MKDOCS
    assert [f.format for f in output.files] == [DocFormat.MARKDOWN, DocFormat.RST]
    assert output.files[1].title is None
    assert {f.value for f in DocFramework} == {"mkdocs", "sphinx", "none"}


def test_enum_values() -> None:
    assert {k.value for k in SymbolKind} == {"module", "class", "function", "method", "property"}
    assert {g.value for g in DocGap} == {
        "docstring",
        "params",
        "returns",
        "raises",
        "reference_page",
    }
    assert {t.value for t in DriftType} == {
        "param_mismatch",
        "missing_param_doc",
        "stale_param_doc",
        "stale_reference",
        "broken_link",
        "failing_example",
    }


def test_server_info_rejects_non_semver_contract_version() -> None:
    payload = VALID_PAYLOADS[GetServerInfoOutput]
    with pytest.raises(ValidationError, match="contract_version"):
        GetServerInfoOutput.model_validate({**payload, "contract_version": "v1"})


def test_server_capabilities_reject_unknown_tools() -> None:
    payload = VALID_PAYLOADS[GetServerInfoOutput]
    capabilities = {**payload["capabilities"], "tools": ["delete_repo"]}
    with pytest.raises(ValidationError, match="tools"):
        GetServerInfoOutput.model_validate({**payload, "capabilities": capabilities})


def test_run_doc_examples_output() -> None:
    output = RunDocExamplesOutput.model_validate(VALID_PAYLOADS[RunDocExamplesOutput])
    assert output.results[0].passed is False


def test_translate_output_and_read_output() -> None:
    translated = TranslateFileOutput.model_validate(VALID_PAYLOADS[TranslateFileOutput])
    assert translated.is_new_file is True
    assert ReadFileOutput.model_validate(VALID_PAYLOADS[ReadFileOutput]).total_lines == 1
    assert ProposePatchOutput.model_validate(VALID_PAYLOADS[ProposePatchOutput]).patch
    assert GetSymbolInput.model_validate(VALID_PAYLOADS[GetSymbolInput]).qualname
    assert ListDocFilesInput.model_validate(VALID_PAYLOADS[ListDocFilesInput]).repo == REPO
