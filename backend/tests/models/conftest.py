from collections.abc import AsyncIterator

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.db import create_all, create_engine, create_sessionmaker
from backend.models import Project, Session


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_engine("sqlite+aiosqlite://")
    try:
        await create_all(engine)
        async with create_sessionmaker(engine)() as session:
            yield session
    finally:
        await engine.dispose()


@pytest.fixture
async def chat_session(db_session: AsyncSession) -> Session:
    project = Project(name="example", path="/example")
    db_session.add(project)
    await db_session.flush()
    assert project.id is not None
    session = Session(project_id=project.id, mode="add")
    db_session.add(session)
    await db_session.commit()
    return session
