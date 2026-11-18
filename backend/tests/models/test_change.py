from datetime import UTC

import pytest
from sqlalchemy import UniqueConstraint
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.models import Change, ChangeHunk, ChangeSet, Comment, Session
from backend.models.change import ChangeStatus


@pytest.mark.parametrize("status", list(ChangeStatus))
async def test_changes_and_hunks_round_trip_all_statuses(
    db_session: AsyncSession, chat_session: Session, status: ChangeStatus
) -> None:
    assert chat_session.id is not None
    change_set = ChangeSet(session_id=chat_session.id, run_id="run")
    db_session.add(change_set)
    await db_session.flush()
    assert change_set.id is not None
    original = Change(
        change_set_id=change_set.id,
        file_path="README.md",
        patch="patch",
        rationale="rationale",
        original_content="before",
        proposed_content="after",
    )
    db_session.add(original)
    await db_session.flush()
    assert original.id is not None
    revision = Change(
        change_set_id=change_set.id,
        file_path="README.md",
        patch="revised patch",
        rationale="updated rationale",
        status=status,
        parent_change_id=original.id,
        original_content="before",
        proposed_content="revised",
    )
    db_session.add(revision)
    await db_session.flush()
    assert revision.id is not None
    hunk = ChangeHunk(change_id=revision.id, index=0, header="@@ -1 +1 @@", status=status)
    db_session.add(hunk)
    await db_session.commit()
    db_session.expunge_all()

    saved_set = await db_session.get(ChangeSet, change_set.id)
    saved = await db_session.get(Change, revision.id)
    saved_hunk = await db_session.get(ChangeHunk, hunk.id)
    assert saved_set is not None
    assert saved_set.session_id == chat_session.id
    assert saved_set.run_id == "run"
    assert saved_set.created_at.tzinfo is UTC
    assert saved is not None
    assert saved.status is status
    assert saved.parent_change_id == original.id
    assert saved.created_at.tzinfo is UTC
    assert saved.original_content == "before"
    assert saved.proposed_content == "revised"
    assert saved_hunk is not None
    assert saved_hunk.status is status
    assert saved_hunk.index == 0
    assert original.status is ChangeStatus.PENDING


def test_change_table_links_indexes_and_hunk_order_constraint() -> None:
    tables = SQLModel.metadata.tables
    assert {key.target_fullname for key in tables["change_sets"].foreign_keys} == {"sessions.id"}
    assert {key.target_fullname for key in tables["changes"].foreign_keys} == {
        "change_sets.id",
        "changes.id",
    }
    assert {key.target_fullname for key in tables["change_hunks"].foreign_keys} == {"changes.id"}
    assert tables["change_sets"].c.session_id.index
    assert tables["change_sets"].c.run_id.unique
    assert tables["changes"].c.change_set_id.index
    assert tables["change_hunks"].c.change_id.index
    assert any(
        isinstance(constraint, UniqueConstraint)
        and {column.name for column in constraint.columns} == {"change_id", "index"}
        for constraint in tables["change_hunks"].constraints
    )
    assert {key.target_fullname for key in tables["comments"].foreign_keys} == {"changes.id"}
    assert tables["comments"].c.change_id.index


@pytest.mark.parametrize("hunk_index", [None, 0])
async def test_comments_round_trip(
    db_session: AsyncSession, chat_session: Session, hunk_index: int | None
) -> None:
    assert chat_session.id is not None
    change_set = ChangeSet(session_id=chat_session.id, run_id="comment-run")
    db_session.add(change_set)
    await db_session.flush()
    assert change_set.id is not None
    change = Change(
        change_set_id=change_set.id,
        file_path="README.md",
        patch="patch",
        rationale="rationale",
        original_content="before",
        proposed_content="after",
    )
    db_session.add(change)
    await db_session.flush()
    assert change.id is not None
    comment = Comment(change_id=change.id, hunk_index=hunk_index, text="  More detail\n")
    db_session.add(comment)
    await db_session.commit()
    db_session.expunge_all()

    saved = await db_session.get(Comment, comment.id)

    assert saved is not None
    assert saved.change_id == change.id
    assert saved.hunk_index == hunk_index
    assert saved.text == "  More detail\n"
    assert saved.created_at.tzinfo is UTC
