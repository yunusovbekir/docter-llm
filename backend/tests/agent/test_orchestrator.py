"""Scripted LLM conversations with the actual stdio MCP client and stub server."""

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator, Mapping, Sequence
from contextlib import aclosing
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from mcp.types import Tool
from pydantic import TypeAdapter, ValidationError

from backend.agent import AgentEvent, ChangeSink, InMemoryChangeSink, Mode, Orchestrator, events
from backend.agent.changes import Error as SinkError
from backend.agent.changes import Ok
from backend.config import Settings
from backend.llm import ChatEvent, Done, LLMError, TextDelta, ToolCallRequest, Usage
from backend.mcp_client import MCPError, MCPManager, MCPTimeoutError, ToolResult
from backend.models.project import Project
from docter_contract import ProposePatchOutput, TranslateFileOutput

PATCH = {
    "file_path": "README.md",
    "patch": "--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-Doc\n+Docs\n",
    "rationale": "Fix the heading",
}
TRANSLATION = {
    "source_path": "README.md",
    "target_path": "README.de.md",
    "patch": "--- a/README.de.md\n+++ b/README.de.md\n@@ -1 +1 @@\n-Alt\n+Dokumentation\n",
    "rationale": "Translate documentation into German",
    "is_new_file": False,
    "warnings": [],
}


class ScriptedLLM:
    """Replay one event sequence per completion and retain independent request snapshots."""

    def __init__(self, *turns: Sequence[ChatEvent | LLMError]) -> None:
        self.turns = list(turns)
        self.messages: list[list[Mapping[str, Any]]] = []
        self.tools: list[list[Tool]] = []
        self.closed = 0

    async def stream_chat(
        self, messages: Sequence[Mapping[str, Any]], tools: Sequence[Tool] = ()
    ) -> AsyncGenerator[ChatEvent, None]:
        """Yield exactly the scripted events; exhaustion is a test failure."""
        self.messages.append(deepcopy(list(messages)))
        self.tools.append(deepcopy(list(tools)))
        assert self.turns, "Unexpected LLM completion"
        try:
            for event in self.turns.pop(0):
                if isinstance(event, LLMError):
                    raise event
                yield event
        finally:
            self.closed += 1


@pytest.fixture
async def manager(stub_mcp_command: list[str], tmp_path: Path) -> AsyncIterator[MCPManager]:
    mcp = MCPManager([*stub_mcp_command, "--agent-tools"], errlog_path=tmp_path / "mcp-server.log")
    try:
        yield mcp
    finally:
        await mcp.close()


@pytest.fixture
def translation_manager(manager: MCPManager, monkeypatch: pytest.MonkeyPatch) -> MCPManager:
    monkeypatch.setattr(
        manager,
        "list_tools",
        AsyncMock(return_value=[Tool(name="translate_file", input_schema={})]),
    )
    return manager


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return Project(id=1, name="example", path=str(tmp_path))


def _run(
    llm: ScriptedLLM,
    manager: MCPManager,
    sink: ChangeSink,
    project: Project,
    *,
    mode: Mode = Mode.ADD,
    mode_params: Mapping[str, Any] | None = None,
    max_steps: int = 25,
) -> AsyncGenerator[AgentEvent, None]:
    return Orchestrator(llm, manager, sink, max_agent_steps=max_steps).run(
        [], "Improve the docs", mode, {} if mode_params is None else mode_params, project
    )


def _tool_messages(llm: ScriptedLLM, turn: int = -1) -> list[Mapping[str, Any]]:
    return [message for message in llm.messages[turn] if message["role"] == "tool"]


async def test_streams_text_and_preserves_history_and_task_context(
    manager: MCPManager, project: Project
) -> None:
    llm = ScriptedLLM([TextDelta("Hello"), TextDelta(" docs"), Usage(2, 3, None), Done()])
    sink = InMemoryChangeSink()
    history = [{"role": "user", "content": "Earlier request"}]
    original = deepcopy(history)
    run = Orchestrator(llm, manager, sink).run(
        history, "Translate this", Mode.TRANSLATE, {"target_language": "de"}, project
    )

    result = [event async for event in run]

    assert result == [
        events.Token(text="Hello"),
        events.Token(text=" docs"),
        events.Done(reason="completed", steps=1),
    ]
    assert history == original
    system = llm.messages[0][0]["content"]
    assert system.startswith(await Mode.TRANSLATE.load_system_prompt())
    context = json.loads(system.split("\n\nTask context:\n", 1)[1])
    assert context == {
        "project": {"name": project.name, "path": project.path},
        "mode": "translate",
        "mode_params": {"target_language": "de"},
    }
    assert llm.messages[0][1:] == [*history, {"role": "user", "content": "Translate this"}]
    assert sink.changes == []
    assert llm.closed == 1


async def test_executes_allowed_tools_and_replays_complete_assistant_message(
    manager: MCPManager, project: Project
) -> None:
    llm = ScriptedLLM(
        [
            TextDelta("Reading"),
            ToolCallRequest("read", "read_file", {"file_path": "README.md", "repo": "/other"}),
            ToolCallRequest("patch", "propose_patch", dict(PATCH)),
            Done(),
        ],
        [TextDelta("Proposed"), Done()],
    )
    sink = InMemoryChangeSink()

    result = [event async for event in _run(llm, manager, sink, project)]

    assert [event.type for event in result] == [
        "token",
        "tool_call",
        "tool_call",
        "tool_result",
        "tool_result",
        "change_proposed",
        "token",
        "done",
    ]
    assert {tool.name for tool in llm.tools[0]} == {"read_file", "propose_patch"}
    assistant = llm.messages[1][-3]
    assert assistant["role"] == "assistant"
    assert assistant["content"] == "Reading"
    assert [call["id"] for call in assistant["tool_calls"]] == ["read", "patch"]
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {
        "file_path": "README.md",
        "repo": project.path,
    }
    responses = _tool_messages(llm)
    assert [message["tool_call_id"] for message in responses] == ["read", "patch"]
    assert project.path in responses[0]["content"]
    assert sink.changes == [ProposePatchOutput(**PATCH, is_new_file=False)]
    assert result[-1] == events.Done(reason="completed", steps=2)
    assert llm.closed == 2
    adapter = TypeAdapter(AgentEvent)
    for event in result:
        assert adapter.validate_json(event.model_dump_json()) == event


@pytest.mark.parametrize("name", ["propose_patch", "echo", "index_repo", "unknown", "search_code"])
async def test_blocks_forbidden_and_unavailable_tools_without_calling_mcp(
    name: str, manager: MCPManager, project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    call_tool = AsyncMock(side_effect=AssertionError("Blocked tool reached MCP"))
    monkeypatch.setattr(manager, "call_tool", call_tool)
    llm = ScriptedLLM(
        [ToolCallRequest("blocked", name, dict(PATCH)), Done()],
        [TextDelta("Understood"), Done()],
    )
    sink = InMemoryChangeSink()

    result = [event async for event in _run(llm, manager, sink, project, mode=Mode.FIND_MISSING)]

    call_tool.assert_not_awaited()
    assert {tool.name for tool in llm.tools[0]} == {"read_file"}
    response = _tool_messages(llm)[0]
    assert response["tool_call_id"] == "blocked"
    assert name in response["content"]
    assert "not allowed or unavailable" in response["content"]
    assert any(isinstance(event, events.ToolResult) and event.is_error for event in result)
    assert sink.changes == []
    assert result[-1] == events.Done(reason="completed", steps=2)


async def test_tool_error_is_text_feedback_and_llm_can_retry(
    manager: MCPManager, project: Project
) -> None:
    llm = ScriptedLLM(
        [ToolCallRequest("bad", "read_file", {"file_path": "missing.md"}), Done()],
        [ToolCallRequest("good", "read_file", {"file_path": "README.md"}), Done()],
        [TextDelta("Recovered"), Done()],
    )
    result = [event async for event in _run(llm, manager, InMemoryChangeSink(), project)]

    responses = [event for event in result if isinstance(event, events.ToolResult)]
    assert [response.is_error for response in responses] == [True, False]
    assert "File not found: missing.md" in _tool_messages(llm, 1)[0]["content"]
    assert _tool_messages(llm)[1]["tool_call_id"] == "good"
    assert result[-1] == events.Done(reason="completed", steps=3)


@pytest.mark.parametrize("failure", [MCPError("Connection lost"), MCPTimeoutError("Timed out")])
async def test_transport_errors_are_retryable_tool_feedback(
    failure: MCPError, manager: MCPManager, project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = manager.call_tool
    call_tool = AsyncMock(side_effect=[failure, ToolResult(text="Recovered")])
    monkeypatch.setattr(manager, "call_tool", call_tool)
    llm = ScriptedLLM(
        [ToolCallRequest("first", "read_file", {"file_path": "README.md"}), Done()],
        [ToolCallRequest("retry", "read_file", {"file_path": "README.md"}), Done()],
        [Done()],
    )

    result = [event async for event in _run(llm, manager, InMemoryChangeSink(), project)]

    assert failure.message in _tool_messages(llm, 1)[0]["content"]
    assert result[-1] == events.Done(reason="completed", steps=3)
    assert call_tool.await_count == 2
    monkeypatch.setattr(manager, "call_tool", original)


async def test_sink_error_is_returned_to_llm_before_retry(
    manager: MCPManager, project: Project
) -> None:
    class RejectOnce:
        def __init__(self) -> None:
            self.accepted = InMemoryChangeSink()

        async def add(self, change: ProposePatchOutput) -> Ok | SinkError:
            if change.rationale == PATCH["rationale"]:
                return SinkError("Patch is stale; read the file and regenerate it")
            return await self.accepted.add(change)

    sink = RejectOnce()
    revised = {**PATCH, "rationale": "Regenerated against current file"}
    llm = ScriptedLLM(
        [ToolCallRequest("stale", "propose_patch", dict(PATCH)), Done()],
        [ToolCallRequest("revised", "propose_patch", revised), Done()],
        [Done()],
    )

    result = [event async for event in _run(llm, manager, sink, project)]

    assert _tool_messages(llm, 1)[0]["content"] == (
        "Tool error: Patch is stale; read the file and regenerate it"
    )
    assert sink.accepted.changes == [ProposePatchOutput(**revised, is_new_file=False)]
    proposed = [event for event in result if isinstance(event, events.ChangeProposed)]
    assert len(proposed) == 1
    assert proposed[0].change == sink.accepted.changes[0]
    responses = [event for event in result if isinstance(event, events.ToolResult)]
    assert [response.is_error for response in responses] == [True, False]
    assert responses[0].structured is None


@pytest.mark.parametrize("structured", [None, {"patch": "incomplete"}])
async def test_invalid_patch_results_are_retryable_not_stored(
    structured: object, manager: MCPManager, project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        manager, "call_tool", AsyncMock(return_value=ToolResult(text="bad", structured=structured))
    )
    llm = ScriptedLLM(
        [ToolCallRequest("bad-patch", "propose_patch", dict(PATCH)), Done()], [Done()]
    )
    sink = InMemoryChangeSink()

    result = [event async for event in _run(llm, manager, sink, project)]

    assert "invalid ProposePatchOutput" in _tool_messages(llm)[0]["content"]
    assert not sink.changes
    assert not any(isinstance(event, events.ChangeProposed) for event in result)


@pytest.mark.parametrize("is_new_file", [False, True])
async def test_legacy_text_patch_result_is_validated_and_stored(
    is_new_file: bool, manager: MCPManager, project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    change = ProposePatchOutput(**PATCH, is_new_file=is_new_file)
    monkeypatch.setattr(
        manager, "call_tool", AsyncMock(return_value=ToolResult(text=change.model_dump_json()))
    )
    llm = ScriptedLLM([ToolCallRequest("patch", "propose_patch", dict(PATCH)), Done()], [Done()])
    sink = InMemoryChangeSink()

    result = [event async for event in _run(llm, manager, sink, project)]

    assert sink.changes == [change]
    assert any(isinstance(event, events.ChangeProposed) for event in result)


@pytest.mark.parametrize("is_new_file", [False, True])
@pytest.mark.parametrize("structured", [False, True], ids=["json-text", "structured"])
@pytest.mark.parametrize(
    "warnings", [[], ["Glossary has no German entry", "Check the term 'backoff'"]]
)
async def test_translation_is_proposed_for_target_without_writing_files(
    is_new_file: bool,
    structured: bool,
    warnings: list[str],
    translation_manager: MCPManager,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Path(project.path) / "README.md"
    target = Path(project.path) / "README.de.md"
    source.write_text("Doc\n")
    if not is_new_file:
        target.write_text("Alt\n")
    patch = (
        "--- /dev/null\n+++ b/README.de.md\n@@ -0,0 +1 @@\n+Dokumentation\n"
        if is_new_file
        else TRANSLATION["patch"]
    )
    translation = TranslateFileOutput.model_validate(
        {**TRANSLATION, "patch": patch, "is_new_file": is_new_file, "warnings": warnings}
    )
    response = (
        ToolResult(text="", structured=translation.model_dump())
        if structured
        else ToolResult(text=translation.model_dump_json())
    )
    call_tool = AsyncMock(return_value=response)
    monkeypatch.setattr(translation_manager, "call_tool", call_tool)
    llm = ScriptedLLM(
        [
            ToolCallRequest(
                "translation",
                "translate_file",
                {"file_path": "README.md", "target_language": "de", "repo": "/other"},
            ),
            Done(),
        ],
        [Done()],
    )
    sink = InMemoryChangeSink()

    result = [
        event
        async for event in _run(
            llm,
            translation_manager,
            sink,
            project,
            mode=Mode.TRANSLATE,
            mode_params={"target_language": "de"},
        )
    ]

    expected = ProposePatchOutput(
        file_path="README.de.md",
        patch=translation.patch,
        rationale=translation.rationale,
        is_new_file=is_new_file,
    )
    assert sink.changes == [expected]
    assert [event.type for event in result] == [
        "tool_call",
        "tool_result",
        *(["warning"] * len(warnings)),
        "change_proposed",
        "done",
    ]
    assert result[1] == events.ToolResult(
        id="translation",
        name="translate_file",
        text=translation.model_dump_json() if not structured else json.dumps(response.structured),
        structured=response.structured,
    )
    assert result[2:-2] == [
        events.Warning(code="translation_warning", message=warning) for warning in warnings
    ]
    assert result[-2] == events.ChangeProposed(change=expected)
    assert result[-1] == events.Done(reason="completed", steps=2)
    assert _tool_messages(llm)[0]["content"] == result[1].text
    call_tool.assert_awaited_once_with(
        "translate_file",
        {"file_path": "README.md", "target_language": "de", "repo": project.path},
        timeout=Settings.DEFAULT_REQUEST_TIMEOUT_S,
    )
    assert source.read_text() == "Doc\n"
    if is_new_file:
        assert not target.exists()
    else:
        assert target.read_text() == "Alt\n"


@pytest.mark.parametrize("structured", [False, True], ids=["json-text", "structured"])
@pytest.mark.parametrize(
    "payload",
    [
        {"patch": "incomplete"},
        {key: value for key, value in TRANSLATION.items() if key != "source_path"},
        {**TRANSLATION, "target_path": ""},
        {**TRANSLATION, "warnings": "not a list"},
        {**TRANSLATION, "warnings": [42]},
        {**TRANSLATION, "is_new_file": "maybe"},
        {**TRANSLATION, "unexpected": True},
    ],
)
async def test_invalid_translation_output_is_retryable_not_proposed(
    structured: bool,
    payload: object,
    translation_manager: MCPManager,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = (
        ToolResult(text=json.dumps(TRANSLATION), structured=payload)
        if structured
        else ToolResult(text=json.dumps(payload))
    )
    monkeypatch.setattr(translation_manager, "call_tool", AsyncMock(return_value=response))
    llm = ScriptedLLM([ToolCallRequest("bad", "translate_file", {}), Done()], [Done()])
    sink = InMemoryChangeSink()

    result = [
        event
        async for event in _run(
            llm,
            translation_manager,
            sink,
            project,
            mode=Mode.TRANSLATE,
            mode_params={"target_language": "de"},
        )
    ]

    assert [event.type for event in result] == ["tool_call", "tool_result", "done"]
    assert result[1] == events.ToolResult(
        id="bad",
        name="translate_file",
        text="Tool error: translate_file returned an invalid TranslateFileOutput",
        is_error=True,
    )
    assert _tool_messages(llm)[0]["content"] == result[1].text
    assert result[-1] == events.Done(reason="completed", steps=2)
    assert not sink.changes


@pytest.mark.parametrize("text", ["not JSON", "", "[]", "null"])
async def test_malformed_translation_json_is_retryable_not_proposed(
    text: str,
    translation_manager: MCPManager,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        translation_manager, "call_tool", AsyncMock(return_value=ToolResult(text=text))
    )
    llm = ScriptedLLM([ToolCallRequest("bad", "translate_file", {}), Done()], [Done()])
    sink = InMemoryChangeSink()

    result = [
        event
        async for event in _run(
            llm,
            translation_manager,
            sink,
            project,
            mode=Mode.TRANSLATE,
            mode_params={"target_language": "de"},
        )
    ]

    assert [event.type for event in result] == ["tool_call", "tool_result", "done"]
    assert _tool_messages(llm)[0]["content"] == (
        "Tool error: translate_file returned an invalid TranslateFileOutput"
    )
    assert not sink.changes


@pytest.mark.parametrize(
    "failure",
    [
        ToolResult(
            text="Translation failed",
            structured={**TRANSLATION, "warnings": ["Untrusted warning"]},
            is_error=True,
        ),
        MCPError("Connection lost"),
        MCPTimeoutError("Timed out"),
    ],
)
async def test_translation_tool_errors_are_retryable_not_proposed(
    failure: ToolResult | MCPError,
    translation_manager: MCPManager,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(translation_manager, "call_tool", AsyncMock(side_effect=[failure]))
    llm = ScriptedLLM([ToolCallRequest("failed", "translate_file", {}), Done()], [Done()])
    sink = InMemoryChangeSink()

    result = [
        event
        async for event in _run(
            llm,
            translation_manager,
            sink,
            project,
            mode=Mode.TRANSLATE,
            mode_params={"target_language": "de"},
        )
    ]

    message = failure.message if isinstance(failure, MCPError) else failure.text
    assert _tool_messages(llm)[0]["content"] == f"Tool error: {message}"
    assert [event.type for event in result] == ["tool_call", "tool_result", "done"]
    assert isinstance(result[1], events.ToolResult) and result[1].is_error
    assert result[-1] == events.Done(reason="completed", steps=2)
    assert not sink.changes


@pytest.mark.parametrize("structured", [False, True], ids=["json-text", "structured"])
async def test_translation_sink_rejection_is_retryable_not_proposed(
    structured: bool,
    translation_manager: MCPManager,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    translation = TranslateFileOutput.model_validate(
        {**TRANSLATION, "warnings": ["Check terminology"]}
    )
    response = (
        ToolResult(text="", structured=translation.model_dump())
        if structured
        else ToolResult(text=translation.model_dump_json())
    )
    monkeypatch.setattr(translation_manager, "call_tool", AsyncMock(return_value=response))
    sink = InMemoryChangeSink()
    add = AsyncMock(return_value=SinkError("Target changed; regenerate the translation"))
    monkeypatch.setattr(sink, "add", add)
    llm = ScriptedLLM([ToolCallRequest("rejected", "translate_file", {}), Done()], [Done()])

    result = [
        event
        async for event in _run(
            llm,
            translation_manager,
            sink,
            project,
            mode=Mode.TRANSLATE,
            mode_params={"target_language": "de"},
        )
    ]

    add.assert_awaited_once_with(
        ProposePatchOutput(
            file_path=translation.target_path,
            patch=translation.patch,
            rationale=translation.rationale,
            is_new_file=translation.is_new_file,
        )
    )
    assert [event.type for event in result] == ["tool_call", "tool_result", "warning", "done"]
    assert result[1] == events.ToolResult(
        id="rejected",
        name="translate_file",
        text="Tool error: Target changed; regenerate the translation",
        is_error=True,
    )
    assert _tool_messages(llm)[0]["content"] == result[1].text
    assert result[2] == events.Warning(code="translation_warning", message="Check terminology")
    assert result[-1] == events.Done(reason="completed", steps=2)
    assert not sink.changes


@pytest.mark.parametrize("mode", [Mode.ADD, Mode.FIX, Mode.FIND_MISSING])
async def test_translation_is_blocked_outside_translate_mode(
    mode: Mode,
    translation_manager: MCPManager,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_tool = AsyncMock(side_effect=AssertionError("Blocked translation reached MCP"))
    monkeypatch.setattr(translation_manager, "call_tool", call_tool)
    llm = ScriptedLLM([ToolCallRequest("blocked", "translate_file", {}), Done()], [Done()])
    sink = InMemoryChangeSink()

    result = [event async for event in _run(llm, translation_manager, sink, project, mode=mode)]

    call_tool.assert_not_awaited()
    assert "translate_file" not in {tool.name for tool in llm.tools[0]}
    assert (
        "Tool error: Tool 'translate_file' is not allowed or unavailable"
        in (_tool_messages(llm)[0]["content"])
    )
    assert [event.type for event in result] == ["tool_call", "tool_result", "done"]
    assert not sink.changes


async def test_max_steps_bounds_llm_rounds_not_number_of_tools(
    manager: MCPManager, project: Project
) -> None:
    llm = ScriptedLLM(
        [ToolCallRequest("a", "read_file", {"file_path": "README.md"}), Done()],
        [
            ToolCallRequest("b", "read_file", {"file_path": "README.md"}),
            ToolCallRequest("c", "read_file", {"file_path": "README.md"}),
            Done(),
        ],
    )

    result = [
        event async for event in _run(llm, manager, InMemoryChangeSink(), project, max_steps=2)
    ]

    assert len(llm.messages) == 2
    assert len([event for event in result if isinstance(event, events.ToolResult)]) == 3
    assert isinstance(result[-2], events.Warning)
    assert result[-2].code == "max_agent_steps"
    assert "(2)" in result[-2].message
    assert result[-1] == events.Done(reason="max_steps", steps=2)


async def test_final_answer_at_step_limit_does_not_warn(
    manager: MCPManager, project: Project
) -> None:
    llm = ScriptedLLM([TextDelta("Finished"), Done()])
    result = [
        event async for event in _run(llm, manager, InMemoryChangeSink(), project, max_steps=1)
    ]
    assert result == [events.Token(text="Finished"), events.Done(reason="completed", steps=1)]


@pytest.mark.parametrize("limit", [0, -1, 201])
def test_rejects_invalid_step_limits(limit: int) -> None:
    with pytest.raises(ValidationError):
        Orchestrator(
            ScriptedLLM(), MCPManager(["unused"]), InMemoryChangeSink(), max_agent_steps=limit
        )


@pytest.mark.parametrize(
    "turn",
    [
        [TextDelta("Partial"), LLMError("Provider failed")],
        [ToolCallRequest("patch", "propose_patch", dict(PATCH)), LLMError("Provider failed")],
        [ToolCallRequest("patch", "propose_patch", dict(PATCH))],
    ],
)
async def test_failed_or_incomplete_llm_stream_never_executes_tools(
    turn: Sequence[ChatEvent | LLMError],
    manager: MCPManager,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_tool = AsyncMock(side_effect=AssertionError("Incomplete turn executed a tool"))
    monkeypatch.setattr(manager, "call_tool", call_tool)
    llm = ScriptedLLM(turn)
    sink = InMemoryChangeSink()

    result = [event async for event in _run(llm, manager, sink, project)]

    call_tool.assert_not_awaited()
    assert not sink.changes
    assert isinstance(result[-2], events.Error)
    assert result[-2].code == "llm_error"
    assert result[-1] == events.Done(reason="error", steps=1)
    assert llm.closed == 1


@pytest.mark.parametrize(
    "mode_params",
    [
        {},
        {"target_language": "Japanese"},
        {"target_language": ""},
        {"target_language": " \t\n"},
        {"target_language": " ja"},
        {"target_language": None},
        {"target_language": 42},
        {"target_language": False},
        {"target_language": []},
    ],
)
async def test_invalid_mode_params_fail_before_mcp_or_llm(
    mode_params: dict[str, object],
    manager: MCPManager,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    list_tools = AsyncMock(side_effect=AssertionError("Invalid mode params reached MCP discovery"))
    call_tool = AsyncMock(side_effect=AssertionError("Invalid mode params reached an MCP tool"))
    monkeypatch.setattr(manager, "list_tools", list_tools)
    monkeypatch.setattr(manager, "call_tool", call_tool)
    llm = ScriptedLLM()
    sink = InMemoryChangeSink()
    result = [
        event
        async for event in _run(
            llm, manager, sink, project, mode=Mode.TRANSLATE, mode_params=mode_params
        )
    ]
    list_tools.assert_not_awaited()
    call_tool.assert_not_awaited()
    assert not llm.messages
    assert not llm.tools
    assert not sink.changes
    assert not manager.is_running
    assert len(result) == 2
    assert isinstance(result[0], events.Error)
    assert result[0].code == "invalid_mode_params"
    assert "language tag" in result[0].message
    assert result[-1] == events.Done(reason="error", steps=0)


async def test_tool_discovery_failure_is_terminal(
    manager: MCPManager, project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(manager, "list_tools", AsyncMock(side_effect=MCPError("Cannot start MCP")))
    result = [event async for event in _run(ScriptedLLM(), manager, InMemoryChangeSink(), project)]
    assert result == [
        events.Error(code="mcp_error", message="Cannot start MCP"),
        events.Done(reason="error", steps=0),
    ]


async def test_closing_iterator_closes_suspended_llm_stream(
    manager: MCPManager, project: Project
) -> None:
    llm = ScriptedLLM([TextDelta("First"), TextDelta("Second"), Done()])
    async with aclosing(_run(llm, manager, InMemoryChangeSink(), project)) as run:
        assert await anext(run) == events.Token(text="First")
    assert llm.closed == 1


async def test_task_cancellation_closes_llm_without_done_or_tool_execution(
    manager: MCPManager, project: Project
) -> None:
    started = asyncio.Event()
    closed = asyncio.Event()

    class WaitingLLM:
        async def stream_chat(
            self, messages: Sequence[Mapping[str, Any]], tools: Sequence[Tool] = ()
        ) -> AsyncGenerator[ChatEvent, None]:
            try:
                yield TextDelta("Started")
                started.set()
                await asyncio.Event().wait()
            finally:
                closed.set()

    result: list[AgentEvent] = []
    sink = InMemoryChangeSink()

    async def consume() -> None:
        async for event in Orchestrator(WaitingLLM(), manager, sink).run(
            [], "Read", Mode.ADD, {}, project
        ):
            result.append(event)

    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(started.wait(), timeout=10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    assert closed.is_set()
    assert result == [events.Token(text="Started")]
    assert not sink.changes


async def test_task_cancellation_during_mcp_call_does_not_close_shared_manager(
    manager: MCPManager, project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm = ScriptedLLM(
        [ToolCallRequest("slow", "read_file", {"file_path": "README.md", "delay": 60}), Done()],
    )
    original = manager.call_tool
    invoked = asyncio.Event()
    cancelled = asyncio.Event()

    async def call_tool(
        name: str,
        args: Mapping[str, Any] | None,
        timeout: float,  # noqa: ASYNC109
    ) -> ToolResult:
        invoked.set()
        try:
            return await original(name, args, timeout)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(manager, "call_tool", call_tool)
    result: list[AgentEvent] = []

    async def consume() -> None:
        async for event in _run(llm, manager, InMemoryChangeSink(), project):
            result.append(event)

    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(invoked.wait(), timeout=10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    assert cancelled.is_set()
    assert [event.type for event in result] == ["tool_call"]
    assert manager.is_running
    assert (await original("echo", {"text": "still usable"}, timeout=10)).text == "still usable"
