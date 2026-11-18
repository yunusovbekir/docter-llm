"""Export the API schema with ``uv run python -m backend.scripts.export_openapi``."""

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path

import anyio

from backend.main import create_app


def render_schema() -> str:
    """Render a deterministic schema without starting the app or its services."""
    return json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"


async def export_schema(output: Path, *, check: bool = False) -> int:
    """Write the schema, or return one if a checked artifact is missing or stale."""
    path = anyio.Path(output)
    expected = render_schema()
    if check:
        current = await path.read_text(encoding="utf-8") if await path.exists() else None
        if current != expected:
            print(
                f"{output} is out of date; run python -m backend.scripts.export_openapi",
                file=sys.stderr,
            )
            return 1
        return 0

    await path.parent.mkdir(parents=True, exist_ok=True)
    await path.write_text(expected, encoding="utf-8", newline="\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Parse export/check options and return the command's exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "frontend" / "src" / "api" / "openapi.json",
        help="schema file to write or check",
    )
    parser.add_argument("--check", action="store_true", help="fail if the schema is stale")
    args = parser.parse_args(argv)
    return asyncio.run(export_schema(args.output, check=args.check))


if __name__ == "__main__":
    raise SystemExit(main())
