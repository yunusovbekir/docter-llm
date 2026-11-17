from httpx import AsyncClient


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
