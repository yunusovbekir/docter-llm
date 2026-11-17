"""Export the Docter contract's JSON Schemas to contract/schema.json.

Usage:
    uv run python contract/scripts/export_schema.py           # write the file
    uv run python contract/scripts/export_schema.py --check   # exit 1 if it is stale
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from docter_contract import render_schema


def main(argv: Sequence[str] | None = None) -> int:
    """Write (or, with ``--check``, verify) the schema file and return the exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "schema.json",
        help="schema file to write or check",
    )
    parser.add_argument(
        "--check", action="store_true", help="fail if the file differs instead of writing it"
    )
    args = parser.parse_args(argv)
    output: Path = args.output
    expected = render_schema()

    if args.check:
        current = output.read_text(encoding="utf-8") if output.exists() else None
        if current != expected:
            print(
                f"{output} is out of date; run contract/scripts/export_schema.py", file=sys.stderr
            )
            return 1
        print(f"{output} is up to date", file=sys.stderr)
        return 0

    output.write_text(expected, encoding="utf-8", newline="\n")
    print(f"wrote {output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
