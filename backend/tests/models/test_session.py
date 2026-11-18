from datetime import UTC, timedelta

import pytest
from pydantic import JsonValue, ValidationError
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.models import Session
from backend.models.session import RunStatus
from backend.models.types import utcnow


async def test_session_defaults_and_foreign_key(
    db_session: AsyncSession, chat_session: Session
) -> None:
    db_session.expunge_all()
    saved = await db_session.get(Session, chat_session.id)

    assert saved is not None
    assert saved.id is not None
    assert saved.project_id == chat_session.project_id
    assert saved.mode == "add"
    assert saved.mode_params == {}
    assert saved.branch_name is None
    assert saved.run_status is RunStatus.IDLE
    assert saved.created_at.tzinfo is UTC
    assert saved.updated_at.tzinfo is UTC
    table = SQLModel.metadata.tables["sessions"]
    assert {key.target_fullname for key in table.foreign_keys} == {"projects.id"}
    assert table.c.project_id.index


async def test_session_json_branch_and_status_round_trip(
    db_session: AsyncSession, chat_session: Session
) -> None:
    params: dict[str, JsonValue] = {
        "target_language": "ja",
        "paths": ["README.md", "docs"],
        "options": {"recursive": True, "limit": 3},
        "optional": None,
    }
    chat_session.mode = "translate"
    chat_session.mode_params = params
    chat_session.branch_name = "docter/translation"
    chat_session.run_status = RunStatus.RUNNING
    await db_session.commit()
    db_session.expunge_all()

    saved = await db_session.get(Session, chat_session.id)

    assert saved is not None
    assert saved.mode == "translate"
    assert saved.mode_params == params
    assert saved.branch_name == "docter/translation"
    assert saved.run_status is RunStatus.RUNNING


async def test_updating_session_advances_updated_at_only(
    db_session: AsyncSession, chat_session: Session
) -> None:
    earlier = utcnow() - timedelta(days=1)
    chat_session.created_at = earlier
    chat_session.updated_at = earlier
    await db_session.commit()

    before_update = utcnow()
    chat_session.run_status = RunStatus.RUNNING
    await db_session.commit()
    db_session.expunge_all()
    saved = await db_session.get(Session, chat_session.id)

    assert saved is not None
    assert saved.created_at == earlier
    assert before_update <= saved.updated_at <= utcnow()
    assert saved.updated_at.tzinfo is UTC


def test_mode_params_defaults_are_independent() -> None:
    first = Session(project_id=1, mode="add")
    second = Session(project_id=1, mode="fix")

    first.mode_params["paths"] = ["README.md"]

    assert second.mode_params == {}


@pytest.mark.parametrize("status", ["idle", "running"])
def test_valid_run_status(status: str) -> None:
    session = Session.model_validate({"project_id": 1, "mode": "add", "run_status": status})

    assert session.run_status == status


def test_invalid_run_status_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Session.model_validate({"project_id": 1, "mode": "add", "run_status": "done"})
