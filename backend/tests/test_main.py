import shlex
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.api.deps import MCPManagerDep
from backend.config import Settings
from backend.main import create_app


async def test_root_is_not_served_without_frontend_build(client: AsyncClient) -> None:
    response = await client.get("/")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_cors_allows_vite_dev_server(client: AsyncClient) -> None:
    response = await client.options(
        "/api/health",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


async def test_cors_rejects_other_origins(client: AsyncClient) -> None:
    response = await client.get("/api/health", headers={"Origin": "http://evil.example"})

    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize(
    ("metadata_provider", "vector_provider"), [("sqlite", "pgvector"), ("postgres", "lancedb")]
)
async def test_mcp_manager_is_injected_and_closed_on_shutdown(
    data_dir: Path,
    frontend_dist_dir: Path,
    stub_mcp_command: list[str],
    clean_log_handlers: None,
    monkeypatch: pytest.MonkeyPatch,
    metadata_provider: str,
    vector_provider: str,
) -> None:
    monkeypatch.setenv("DOCTER_DATABASE_URL", f"sqlite+aiosqlite:///{data_dir / 'app.db'}")
    monkeypatch.setenv("DOCTER_APP_ONLY_TEST_SETTING", "not for the MCP process")
    monkeypatch.setenv("DOCTER_INDEX_METADATA_PROVIDER", metadata_provider)
    index_url = "postgresql://docter@localhost/docter_index"
    monkeypatch.setenv("DOCTER_INDEX_METADATA_URL", index_url)
    vector_url = "postgresql://docter@localhost/docter_vectors"
    monkeypatch.setenv("DOCTER_INDEX_VECTOR_PROVIDER", vector_provider)
    monkeypatch.setenv("DOCTER_INDEX_VECTOR_URL", vector_url)
    interpreter, stub = stub_mcp_command
    expected_url = index_url if metadata_provider == "postgres" else None
    expected_vector_url = vector_url if vector_provider == "pgvector" else None
    check_env_then_run_stub = (
        "import os, runpy, sys\n"
        f"assert os.environ['DOCTER_INDEX_METADATA_PROVIDER'] == {metadata_provider!r}\n"
        f"assert os.environ['DOCTER_INDEX_VECTOR_PROVIDER'] == {vector_provider!r}\n"
        f"assert os.environ.get('DOCTER_INDEX_METADATA_URL') == {expected_url!r}\n"
        f"assert os.environ.get('DOCTER_INDEX_VECTOR_URL') == {expected_vector_url!r}\n"
        "assert 'DOCTER_DATABASE_URL' not in os.environ\n"
        "assert 'DOCTER_APP_ONLY_TEST_SETTING' not in os.environ\n"
        "sys.argv = sys.argv[1:]\n"
        "runpy.run_path(sys.argv[0], run_name='__main__')\n"
    )
    settings = Settings(
        data_dir=data_dir,
        frontend_dist_dir=frontend_dist_dir,
        mcp_command=shlex.join([interpreter, "-c", check_env_then_run_stub, stub]),
    )
    app: FastAPI = create_app(settings)

    @app.get("/test/mcp-tools")
    async def tool_names(manager: MCPManagerDep) -> list[str]:
        return sorted(tool.name for tool in await manager.list_tools())

    transport = ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=transport, base_url="http://test") as http_client,
    ):
        response = await http_client.get("/test/mcp-tools")
        manager = app.state.mcp
        assert manager.is_running

    assert response.json() == ["echo", "index_repo", "propose_patch"]
    assert not manager.is_running
    assert (settings.logs_dir / "mcp-server.log").exists()
