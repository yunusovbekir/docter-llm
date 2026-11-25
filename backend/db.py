"""Async database engine, session dependency, and schema creation."""

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager

from fastapi import Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

import backend.models  # noqa: F401  # registers all tables on SQLModel.metadata


def create_engine(database_url: str) -> AsyncEngine:
    """Create the async SQLAlchemy engine for ``database_url``."""
    try:
        return create_async_engine(database_url, hide_parameters=True)
    except ImportError:
        if not database_url.startswith("postgresql+psycopg://"):
            raise
        raise RuntimeError(
            "PostgreSQL requires the optional psycopg driver. Run `uv sync --group postgres`."
        ) from None


@contextmanager
def redact_postgres_errors(engine: AsyncEngine) -> Iterator[None]:
    """Keep driver diagnostics (which may include credentials) out of setup logs."""
    try:
        yield
    except SQLAlchemyError as exc:
        if engine.dialect.name != "postgresql":
            raise
        raise RuntimeError(
            f"PostgreSQL database setup failed ({type(exc).__name__}); "
            "check DOCTER_DATABASE_URL, connectivity, and database permissions."
        ) from None


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create a session factory bound to ``engine``."""
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def create_all(engine: AsyncEngine) -> None:
    """Create all tables registered on ``SQLModel.metadata``."""
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session from the app's session factory."""
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with sessionmaker() as session:
        yield session
