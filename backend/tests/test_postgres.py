"""Opt-in integration coverage against a disposable PostgreSQL database."""

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

from backend.config import Settings
from backend.db import create_engine, redact_postgres_errors
from backend.main import create_app
from backend.models import Message, Project, Session
from backend.models.project import IndexStatus
from backend.models.session import RunStatus

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
async def postgres_url() -> AsyncIterator[SecretStr]:
    dsn = os.getenv("DOCTER_TEST_POSTGRES_URL")
    if not dsn:
        pytest.skip("set DOCTER_TEST_POSTGRES_URL to a disposable PostgreSQL database")
    url = make_url(dsn)
    if url.drivername != "postgresql+psycopg":
        pytest.fail("DOCTER_TEST_POSTGRES_URL must use postgresql+psycopg")

    schema = f"docter_test_{uuid4().hex}"
    engine = create_engine(dsn)
    try:
        with redact_postgres_errors(engine):
            async with engine.begin() as conn:
                await conn.execute(CreateSchema(schema))
        try:
            yield SecretStr(
                url.update_query_dict({"options": f"-c search_path={schema}"}).render_as_string(
                    hide_password=False
                )
            )
        finally:
            with redact_postgres_errors(engine):
                async with engine.begin() as conn:
                    await conn.execute(DropSchema(schema, cascade=True))
    finally:
        await engine.dispose()


@pytest.mark.parametrize("migrate_first", [False, True], ids=["create-all", "alembic"])
async def test_postgres_startup_and_app_state(
    postgres_url: SecretStr,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    clean_log_handlers: None,
    migrate_first: bool,
) -> None:
    monkeypatch.setenv("DOCTER_DATABASE_URL", postgres_url.get_secret_value())
    configured = Settings(data_dir=settings.data_dir, frontend_dist_dir=settings.frontend_dist_dir)
    if migrate_first:
        for args in (("upgrade", "head"), ("check",)):
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "alembic", *args, cwd=ROOT
            )
            assert await process.wait() == 0

    app = create_app(configured)
    transport = ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        assert app.state.engine.dialect.name == "postgresql"
        response = await client.put("/api/settings", json={"model": "postgres-test-model"})
        assert response.status_code == 200, response.text
        async with app.state.sessionmaker() as db:
            project = Project(name="Example", path="/example", index_status=IndexStatus.DONE)
            db.add(project)
            await db.commit()
            assert project.id is not None

        params = {"paths": ["README.md"]}
        response = await client.post(
            "/api/sessions", json={"project_id": project.id, "mode": "add", "mode_params": params}
        )
        assert response.status_code == 201, response.text
        session_id = response.json()["id"]
        async with app.state.sessionmaker() as db:
            session = await db.get(Session, session_id)
            project = await db.get(Project, project.id)
            assert session is not None and project is not None
            session.run_status = RunStatus.RUNNING
            project.index_status = IndexStatus.RUNNING
            db.add(Message(session_id=session_id, role="user", content="Add API docs"))
            await db.commit()

    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        assert (await client.get("/api/settings")).json()["model"] == "postgres-test-model"
        assert (await client.get(f"/api/projects/{project.id}")).json()["index_status"] == "failed"
        body = (await client.get(f"/api/sessions/{session_id}")).json()
        assert body["mode_params"] == params
        assert body["run_status"] == "idle"
        assert [message["content"] for message in body["messages"]] == ["Add API docs"]

    assert not configured.db_path.exists()
