"""The frontend artifact is deterministic and describes the actual error envelope."""

import asyncio
import json
from pathlib import Path

import anyio
from fastapi import FastAPI
from httpx import AsyncClient

from backend.scripts.export_openapi import export_schema, main, render_schema


async def test_openapi_tags_and_error_responses(app: FastAPI, client: AsyncClient) -> None:
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema == json.loads(render_schema())
    tags = {tag["name"] for tag in schema["tags"]}
    assert tags == {"health", "projects", "settings", "sessions", "changes"}
    for path in schema["paths"].values():
        for operation in path.values():
            assert set(operation["tags"]) <= tags
            assert len(operation["tags"]) == 1
            for status in ("400", "404", "409", "422", "500", "502", "503", "504"):
                assert operation["responses"][status]["content"]["application/json"]["schema"] == {
                    "$ref": "#/components/schemas/ErrorResponse"
                }
    detail = schema["components"]["schemas"]["ErrorDetail"]
    assert set(detail["required"]) == {"code", "message"}
    assert set(schema["components"]["schemas"]["ErrorResponse"]["required"]) == {"error"}


async def test_export_and_check_do_not_start_services(tmp_path: Path, data_dir: Path) -> None:
    output = tmp_path / "frontend" / "src" / "api" / "openapi.json"
    assert await export_schema(output, check=True) == 1
    assert not output.exists()
    assert await asyncio.to_thread(main, ["--output", str(output)]) == 0
    assert output.read_text(encoding="utf-8") == render_schema()
    assert await asyncio.to_thread(main, ["--output", str(output), "--check"]) == 0
    output.write_text("{}\n", encoding="utf-8")
    assert await export_schema(output, check=True) == 1
    assert output.read_text(encoding="utf-8") == "{}\n"
    assert not await anyio.Path(data_dir).exists()


async def test_committed_schema_is_current() -> None:
    root = (await anyio.Path(__file__).resolve()).parents[2]
    output = root / "frontend" / "src" / "api" / "openapi.json"
    assert await export_schema(Path(output), check=True) == 0
