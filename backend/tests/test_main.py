import shlex
from pathlib import Path

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


async def test_mcp_manager_is_injected_and_closed_on_shutdown(
    data_dir: Path,
    frontend_dist_dir: Path,
    stub_mcp_command: list[str],
    clean_log_handlers: None,
) -> None:
    settings = Settings(
        data_dir=data_dir,
        frontend_dist_dir=frontend_dist_dir,
        mcp_command=shlex.join(stub_mcp_command),
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
