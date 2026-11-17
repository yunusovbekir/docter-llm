from typing import Annotated

from fastapi import Depends, FastAPI
from httpx import AsyncClient
from sqlalchemy import literal
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.config import Settings
from backend.db import get_session


async def test_session_dependency_executes_queries(app: FastAPI, client: AsyncClient) -> None:
    @app.get("/test/db")
    async def db_probe(session: Annotated[AsyncSession, Depends(get_session)]) -> int:
        result = await session.exec(select(literal(1)))
        return result.one()

    response = await client.get("/test/db")

    assert response.status_code == 200
    assert response.json() == 1


async def test_lifespan_creates_database_and_directories(
    settings: Settings, client: AsyncClient
) -> None:
    assert settings.db_path.is_file()
    assert settings.index_dir.is_dir()
    assert settings.worktrees_dir.is_dir()
    assert settings.logs_dir.is_dir()
