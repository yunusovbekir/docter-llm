import importlib.util
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

import pytest

from docter_contract import (
    CONTRACT_VERSION,
    RESOURCE_URIS,
    TOOL_SPECS,
    ErrorCode,
    ToolName,
    build_schema,
    render_schema,
)

CONTRACT_DIR = Path(__file__).resolve().parent.parent
SCHEMA_FILE = CONTRACT_DIR / "schema.json"
EXPORT_SCRIPT = CONTRACT_DIR / "scripts" / "export_schema.py"

MainFn = Callable[[Sequence[str] | None], int]


def load_export_main() -> MainFn:
    spec = importlib.util.spec_from_file_location("export_schema", EXPORT_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(MainFn, module.main)


def test_schema_snapshot_is_up_to_date() -> None:
    assert SCHEMA_FILE.read_text(encoding="utf-8") == render_schema(), (
        "contract/schema.json is stale. If the change is intended, run "
        "`uv run python contract/scripts/export_schema.py` and bump CONTRACT_VERSION."
    )


def test_schema_document_structure() -> None:
    schema = build_schema()
    assert schema["contract_version"] == CONTRACT_VERSION
    assert schema["error_codes"] == [code.value for code in ErrorCode]
    assert schema["resources"] == list(RESOURCE_URIS)
    assert list(schema["tools"]) == [name.value for name in ToolName]
    for name, spec in TOOL_SPECS.items():
        entry = schema["tools"][name.value]
        assert entry["input"]["title"] == spec.input.__name__
        assert entry["output"]["title"] == spec.output.__name__
        assert entry["input"]["type"] == entry["output"]["type"] == "object"
        assert entry["input"]["additionalProperties"] is False
    assert schema["tools"]["index_repo"]["progress"]["title"] == "IndexProgress"
    assert "progress" not in schema["tools"]["search_code"]


def test_render_schema_is_json_with_trailing_newline() -> None:
    text = render_schema()
    assert text.endswith("}\n")
    assert json.loads(text) == build_schema()


def test_export_script_writes_and_checks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main = load_export_main()
    output = tmp_path / "schema.json"

    assert main(["--output", str(output), "--check"]) == 1
    assert main(["--output", str(output)]) == 0
    assert output.read_bytes() == render_schema().encode("utf-8")
    assert main(["--output", str(output), "--check"]) == 0

    output.write_text("{}\n", encoding="utf-8")
    assert main(["--output", str(output), "--check"]) == 1
    assert capsys.readouterr().out == ""
