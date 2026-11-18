"""Startup repairs only the persisted flags of interrupted work."""

from itertools import product

from fastapi import FastAPI
from sqlmodel import col, select

from backend.config import Settings
from backend.db import create_all, create_engine, create_sessionmaker
from backend.models import Project, Session
from backend.models.project import IndexStatus
from backend.models.session import RunStatus
from backend.models.types import utcnow


async def test_startup_recovers_interrupted_work(
    app: FastAPI, settings: Settings, clean_log_handlers: None
) -> None:
    settings.ensure_dirs()
    engine = create_engine(settings.database_url)
    factory = create_sessionmaker(engine)
    timestamp = utcnow()
    original_projects = []
    original_sessions = []
    try:
        await create_all(engine)
        async with factory() as db:
            for index, (index_status, run_status) in enumerate(product(IndexStatus, RunStatus)):
                project = Project(
                    name=f"project-{index}",
                    path=f"/project-{index}",
                    index_status=index_status,
                    indexed_at=timestamp,
                )
                db.add(project)
                await db.flush()
                assert project.id is not None
                session = Session(
                    project_id=project.id,
                    mode="add",
                    mode_params={"paths": ["README.md"]},
                    branch_name=f"docsmith/{index}",
                    run_status=run_status,
                )
                db.add(session)
                await db.flush()
                original_projects.append(project.model_dump())
                original_sessions.append(session.model_dump(exclude={"updated_at"}))
            await db.commit()
    finally:
        await engine.dispose()

    async with app.router.lifespan_context(app), app.state.sessionmaker() as db:
        projects = (await db.exec(select(Project).order_by(col(Project.id)))).all()
        sessions = (await db.exec(select(Session).order_by(col(Session.id)))).all()
        assert [row.model_dump() for row in projects] == [
            {
                **row,
                "index_status": (
                    IndexStatus.FAILED
                    if row["index_status"] == IndexStatus.RUNNING
                    else row["index_status"]
                ),
            }
            for row in original_projects
        ]
        assert [row.model_dump(exclude={"updated_at"}) for row in sessions] == [
            {**row, "run_status": RunStatus.IDLE} for row in original_sessions
        ]
        snapshot = [row.model_dump() for row in sessions]

    # A second startup must not modify already-recovered rows.
    async with app.router.lifespan_context(app), app.state.sessionmaker() as db:
        sessions = (await db.exec(select(Session).order_by(col(Session.id)))).all()
        assert [row.model_dump() for row in sessions] == snapshot
