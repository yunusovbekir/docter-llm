"""Exercise the explicit Alembic workflow with local SQLite files."""

import os
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from backend.config import Settings
from backend.db import create_all, create_engine, create_sessionmaker
from backend.models import Project, Session


@pytest.fixture
def migration_settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "new-data-dir")


@pytest.fixture
def alembic(migration_settings: Settings) -> Callable[..., str]:
    root = Path(__file__).resolve().parents[2]

    def run(*args: str) -> str:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=root,
            env={**os.environ, "DOCTER_DATA_DIR": str(migration_settings.data_dir)},
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    return run


def test_upgrade_fresh_sqlite(alembic: Callable[..., str], migration_settings: Settings) -> None:
    assert not migration_settings.db_path.exists()
    alembic("upgrade", "head")
    assert "0001 (head)" in alembic("current")
    alembic("check")  # Verify columns, foreign keys, unique constraints, and indexes.
    alembic("upgrade", "head")

    with closing(sqlite3.connect(migration_settings.db_path)) as db:
        tables = db.execute("SELECT name FROM sqlite_schema WHERE type = 'table'").fetchall()
        assert {name for (name,) in tables} == {*SQLModel.metadata.tables, "alembic_version"}
        assert db.execute("SELECT version_num FROM alembic_version").fetchall() == [("0001",)]
        # Alembic's autogenerate check doesn't compare CHECK constraints.
        insert = "INSERT INTO app_settings VALUES (?, 'provider', 'model', NULL, 'embed', 0.2, 25)"
        db.execute(insert, (1,))
        with pytest.raises(sqlite3.IntegrityError, match="single_row"):
            db.execute(insert, (2,))
        db.commit()

    alembic("downgrade", "base")
    with closing(sqlite3.connect(migration_settings.db_path)) as db:
        assert db.execute("SELECT name FROM sqlite_schema WHERE type = 'table'").fetchall() == [
            ("alembic_version",)
        ]
        assert db.execute("SELECT version_num FROM alembic_version").fetchall() == []


async def test_stamp_existing_sqlite_preserves_rows(
    alembic: Callable[..., str], migration_settings: Settings
) -> None:
    migration_settings.ensure_dirs()
    engine = create_engine(migration_settings.database_url)
    factory = create_sessionmaker(engine)
    try:
        await create_all(engine)
        async with factory() as db:
            project = Project(name="Example", path="/example")
            db.add(project)
            await db.flush()
            assert project.id is not None
            session = Session(project_id=project.id, mode="add", mode_params={"paths": ["README"]})
            db.add(session)
            await db.commit()
            assert session.id is not None
            before = project.model_dump(), session.model_dump()

        assert alembic("current").strip() == ""
        alembic("stamp", "0001")
        alembic("upgrade", "head")
        assert "0001 (head)" in alembic("current")
        alembic("check")

        async with factory() as db:
            saved_project = await db.get(Project, project.id)
            saved_session = await db.get(Session, session.id)
            assert saved_project is not None
            assert saved_session is not None
            assert (saved_project.model_dump(), saved_session.model_dump()) == before
    finally:
        await engine.dispose()
