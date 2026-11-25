"""Run explicit schema migrations against the configured app database."""

import asyncio

from alembic import context
from sqlalchemy.engine import Connection, make_url
from sqlmodel import SQLModel

import backend.models  # noqa: F401  # registers the app tables for autogenerate
from backend.config import get_settings
from backend.db import create_engine, redact_postgres_errors


def run_migrations(connection: Connection) -> None:
    sqlite = connection.dialect.name == "sqlite"
    context.configure(
        connection=connection,
        target_metadata=SQLModel.metadata,
        render_as_batch=sqlite,
        transactional_ddl=True,
    )
    with context.begin_transaction():
        if sqlite:
            # SQLite's legacy transaction mode doesn't begin a transaction for DDL.
            connection.exec_driver_sql("BEGIN")
        context.run_migrations()


async def run_online() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    try:
        if engine.dialect.name == "sqlite" and engine.url.database == str(settings.db_path):
            settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        with redact_postgres_errors(engine):
            async with engine.connect() as connection:
                await connection.run_sync(run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    dialect = make_url(get_settings().database_url).get_backend_name()
    context.configure(dialect_name=dialect, transactional_ddl=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(run_online())
