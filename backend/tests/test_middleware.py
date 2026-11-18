"""Request IDs remain isolated and available on success, error, and CORS responses."""

import asyncio
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException, Request
from httpx import AsyncClient


@pytest.mark.parametrize("supplied", [None, "", "has spaces", "x" * 129, "non-ascii-\u00e9"])
async def test_missing_or_invalid_request_id_is_generated(
    client: AsyncClient, supplied: str | None
) -> None:
    headers = {b"X-Request-ID": supplied.encode("latin-1")} if supplied is not None else {}
    first = await client.get("/api/health", headers=headers)
    second = await client.get("/api/health", headers=headers)

    assert UUID(first.headers["X-Request-ID"]).version == 4
    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]


async def test_concurrent_requests_have_isolated_ids(app: FastAPI, client: AsyncClient) -> None:
    @app.get("/test/request-id")
    async def request_id(request: Request) -> dict[str, str]:
        await asyncio.sleep(0)
        return {"request_id": request.state.request_id}

    ids = ["client.1_test-id", "x" * 128]
    responses = await asyncio.gather(
        *(client.get("/test/request-id", headers={"X-Request-ID": value}) for value in ids)
    )
    for value, response in zip(ids, responses, strict=True):
        assert response.json() == {"request_id": value}
        assert response.headers["X-Request-ID"] == value


@pytest.mark.parametrize(
    ("url", "status", "code"),
    [
        ("/api/unknown", 404, "not_found"),
        ("/api/sessions?project_id=invalid", 422, "validation_error"),
        ("/api/projects/999", 404, "project_not_found"),
    ],
)
async def test_request_id_on_handled_errors(
    client: AsyncClient, url: str, status: int, code: str
) -> None:
    response = await client.get(url, headers={"X-Request-ID": "error-request"})

    assert response.status_code == status
    assert response.headers["X-Request-ID"] == "error-request"
    assert response.json()["error"]["code"] == code


async def test_request_id_on_unhandled_error(
    app: FastAPI, client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    @app.get("/test/unhandled")
    async def unhandled() -> None:
        raise RuntimeError("unexpected failure")

    response = await client.get("/test/unhandled", headers={"X-Request-ID": "failed-request"})

    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == "failed-request"
    assert response.json() == {
        "error": {"code": "internal_error", "message": "Internal server error"}
    }
    assert "request_id=failed-request" in caplog.text


async def test_http_error_preserves_headers(app: FastAPI, client: AsyncClient) -> None:
    @app.get("/test/auth")
    async def auth() -> None:
        raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})

    response = await client.get("/test/auth", headers={"X-Request-ID": "auth-request"})

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.headers["X-Request-ID"] == "auth-request"
    assert response.json() == {
        "error": {"code": "unauthorized", "message": "Authentication required"}
    }


async def test_cors_exposes_request_id_and_preflight_has_id(client: AsyncClient) -> None:
    response = await client.get(
        "/api/health",
        headers={"Origin": "http://localhost:5173", "X-Request-ID": "browser-request"},
    )
    assert response.headers["Access-Control-Expose-Headers"] == "X-Request-ID"
    assert response.headers["X-Request-ID"] == "browser-request"

    preflight = await client.options(
        "/api/health",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-Request-ID",
        },
    )
    assert preflight.status_code == 200
    assert UUID(preflight.headers["X-Request-ID"]).version == 4
