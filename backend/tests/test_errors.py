from fastapi import FastAPI
from httpx import AsyncClient

from backend.errors import DocterError


class _TeapotError(DocterError):
    status_code = 418
    code = "teapot"


async def test_unhandled_exception_returns_internal_error(
    app: FastAPI, client: AsyncClient
) -> None:
    @app.get("/test/boom")
    async def boom() -> None:
        raise RuntimeError("secret internals")

    response = await client.get("/test/boom")

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "Internal server error"}
    }


async def test_domain_error_maps_to_its_status_and_code(app: FastAPI, client: AsyncClient) -> None:
    @app.get("/test/teapot")
    async def teapot() -> None:
        raise _TeapotError("short and stout")

    response = await client.get("/test/teapot")

    assert response.status_code == 418
    assert response.json() == {"error": {"code": "teapot", "message": "short and stout"}}


async def test_unknown_route_uses_error_shape(client: AsyncClient) -> None:
    response = await client.get("/api/does-not-exist")

    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Not Found"}}


async def test_validation_error_uses_error_shape(app: FastAPI, client: AsyncClient) -> None:
    @app.get("/test/items/{item_id}")
    async def get_item(item_id: int) -> int:
        return item_id

    response = await client.get("/test/items/abc")

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert "item_id" in body["error"]["message"]
