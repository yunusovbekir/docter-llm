from typing import Annotated
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import Depends, FastAPI
from httpx import AsyncClient
from sqlalchemy import literal
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.config import Settings
from backend.db import get_session
from backend.main import create_app


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


async def test_postgres_startup_failure_does_not_fall_back_or_log_credentials(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, clean_log_handlers: None
) -> None:
    url = "postgresql+psycopg://user:dummy-password@localhost/db"
    monkeypatch.setenv("DOCTER_DATABASE_URL", url)
    configured = Settings(data_dir=settings.data_dir)
    engine = Mock(spec=AsyncEngine)
    engine.dialect.name = "postgresql"
    engine_factory = Mock(return_value=engine)
    monkeypatch.setattr("backend.db.create_async_engine", engine_factory)
    create_tables = AsyncMock(side_effect=OperationalError(None, None, Exception(url)))
    monkeypatch.setattr("backend.main.create_all", create_tables)
    app = create_app(configured)

    with pytest.raises(RuntimeError, match="PostgreSQL database setup failed") as caught:
        async with app.router.lifespan_context(app):
            pytest.fail("Application started after the configured database failed")

    assert "dummy-password" not in str(caught.value)
    assert caught.value.__suppress_context__
    assert not configured.db_path.exists()
    engine_factory.assert_called_once_with(url, hide_parameters=True)
    create_tables.assert_awaited_once()
    engine.dispose.assert_awaited_once()
