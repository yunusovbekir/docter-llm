from pathlib import Path

import pytest
from httpx import AsyncClient


@pytest.fixture
def frontend_dist_dir(tmp_path: Path) -> Path:
    dist = tmp_path / "frontend-dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('app')", encoding="utf-8")
    return dist


async def test_serves_index_at_root(client: AsyncClient) -> None:
    response = await client.get("/")

    assert response.status_code == 200
    assert response.text == "<html>spa</html>"


async def test_serves_static_asset(client: AsyncClient) -> None:
    response = await client.get("/assets/app.js")

    assert response.status_code == 200
    assert response.text == "console.log('app')"


async def test_unknown_client_route_falls_back_to_index(client: AsyncClient) -> None:
    response = await client.get("/projects/42/review")

    assert response.status_code == 200
    assert response.text == "<html>spa</html>"


async def test_api_routes_take_precedence_and_do_not_fall_back(client: AsyncClient) -> None:
    assert (await client.get("/api/health")).json()["status"] == "ok"

    missing = await client.get("/api/unknown")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"
