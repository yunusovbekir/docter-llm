"""Regenerate the MCP README's tool reference from the shared contract."""

import argparse
import sys
from pathlib import Path

from docter_contract import TOOL_SPECS

START = "<!-- BEGIN GENERATED TOOL REFERENCE -->"
END = "<!-- END GENERATED TOOL REFERENCE -->"


def render_reference() -> str:
    """List each tool's flat arguments and link to its structured result schema."""
    lines = [
        "| Tool | Required arguments | Optional arguments | Structured result |",
        "|---|---|---|---|",
    ]
    for name, spec in TOOL_SPECS.items():
        required: list[str] = []
        optional: list[str] = []
        for field, info in spec.input.model_fields.items():
            (required if info.is_required() else optional).append(f"`{field}`")
        result = f"[`{spec.output.__name__}`](../contract/schema.json)"
        lines.append(
            f"| `{name.value}` | {', '.join(required) or '-'} | "
            f"{', '.join(optional) or '-'} | {result} |"
        )
    return "\n".join(lines)


def main() -> int:
    """Update the generated block, or fail if it is out of date."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="check instead of writing")
    args = parser.parse_args()
    readme = Path(__file__).resolve().parents[1] / "README.md"
    raw = readme.read_bytes().decode("utf-8")
    current = raw.replace("\r\n", "\n")
    before, _, remainder = current.partition(START)
    _, end, after = remainder.partition(END)
    if not end:
        raise ValueError(f"Missing generated tool reference markers in {readme}")
    expected = f"{before}{START}\n\n{render_reference()}\n\n{END}{after}"
    if args.check:
        if current == expected:
            return 0
        print(
            "Tool reference is out of date; run mcp_server/scripts/export_tool_reference.py",
            file=sys.stderr,
        )
        return 1
    readme.write_text(expected, encoding="utf-8", newline="\r\n" if "\r\n" in raw else "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
