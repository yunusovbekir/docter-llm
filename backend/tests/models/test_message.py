from datetime import UTC

import pytest
from sqlmodel import SQLModel, col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.models import Message, Session


@pytest.mark.parametrize("role", ["system", "user", "assistant", "tool"])
async def test_message_content_and_defaults(
    db_session: AsyncSession, chat_session: Session, role: str
) -> None:
    assert chat_session.id is not None
    message = Message(session_id=chat_session.id, role=role, content="Documentation\n" * 1000)
    db_session.add(message)
    await db_session.commit()
    db_session.expunge_all()

    saved = await db_session.get(Message, message.id)

    assert saved is not None
    assert saved.id is not None
    assert saved.session_id == chat_session.id
    assert saved.role == role
    assert saved.content == message.content
    assert saved.tool_calls is None
    assert saved.tool_call_id is None
    assert saved.created_at.tzinfo is UTC
    table = SQLModel.metadata.tables["messages"]
    assert {key.target_fullname for key in table.foreign_keys} == {"sessions.id"}
    assert table.c.session_id.index


async def test_assistant_tool_calls_and_correlated_result_round_trip(
    db_session: AsyncSession, chat_session: Session
) -> None:
    assert chat_session.id is not None
    assistant = Message(
        session_id=chat_session.id,
        role="assistant",
        tool_calls=[
            {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": '{"path": "README.md"}',
                },
            }
        ],
    )
    tool = Message(
        session_id=chat_session.id,
        role="tool",
        content="# Example",
        tool_call_id="call-1",
    )
    db_session.add_all([assistant, tool])
    await db_session.commit()
    db_session.expunge_all()

    result = await db_session.exec(
        select(Message)
        .where(Message.session_id == chat_session.id)
        .order_by(col(Message.created_at), col(Message.id))
    )
    saved_assistant, saved_tool = result.all()

    assert saved_assistant.role == "assistant"
    assert saved_assistant.content is None
    assert saved_assistant.tool_calls == assistant.tool_calls
    assert saved_assistant.tool_call_id is None
    assert saved_tool.role == "tool"
    assert saved_tool.content == "# Example"
    assert saved_tool.tool_calls is None
    assert saved_tool.tool_call_id == "call-1"
