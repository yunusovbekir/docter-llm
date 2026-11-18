"""Cancellable, mode-restricted orchestration of streamed LLM and MCP calls."""

import json
from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import aclosing
from copy import deepcopy
from typing import Any, Protocol

from mcp.types import Tool
from pydantic import TypeAdapter, ValidationError

from backend.agent import events
from backend.agent.changes import ChangeSink
from backend.agent.changes import Error as SinkError
from backend.agent.modes import Mode
from backend.config import Settings
from backend.errors import DocterError
from backend.llm.events import ChatEvent, Done, TextDelta, ToolCallRequest
from backend.llm.gateway import LLMError
from backend.mcp_client import MCPError, MCPManager, ToolResult
from backend.models.project import Project
from backend.models.settings import MaxAgentSteps
from docter_contract import ProposePatchOutput, TranslateFileOutput


class ChatModel(Protocol):
    """The streaming gateway interface used by the orchestrator and scripted fakes."""

    def stream_chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Tool] = (),
    ) -> AsyncGenerator[ChatEvent, None]:
        """Stream a single assistant response and close resources on cancellation."""
        ...


class Orchestrator:
    """Run bounded conversations without applying any repository changes."""

    def __init__(
        self,
        llm: ChatModel,
        mcp: MCPManager,
        change_sink: ChangeSink,
        *,
        max_agent_steps: int = Settings.DEFAULT_MAX_AGENT_STEPS,
    ) -> None:
        """Inject a gateway, shared MCP manager, scoped sink, and saved step limit."""
        self._llm = llm
        self._mcp = mcp
        self._change_sink = change_sink
        self._max_agent_steps = TypeAdapter(MaxAgentSteps).validate_python(
            max_agent_steps, strict=True
        )

    async def run(
        self,
        history: Sequence[Mapping[str, Any]],
        user_message: str,
        mode: Mode,
        mode_params: Mapping[str, Any],
        project: Project,
    ) -> AsyncGenerator[events.AgentEvent, None]:
        """Yield agent events until completion, the step limit, or a domain error.

        Task cancellation propagates unchanged. Consumers that stop early must
        close this iterator (for example with ``contextlib.aclosing``).
        """
        steps = 0
        try:
            mode.validate_params(mode_params)
            prompt = await mode.load_system_prompt()
            repo = project.path
            context = {
                "project": {"name": project.name, "path": repo},
                "mode": mode.value,
                "mode_params": dict(mode_params),
            }
            messages: list[Mapping[str, Any]] = [
                {"role": "system", "content": prompt + "\n\nTask context:\n" + json.dumps(context)},
                *deepcopy([dict(message) for message in history]),
                {"role": "user", "content": user_message},
            ]
            tools = [
                tool for tool in await self._mcp.list_tools() if tool.name in mode.allowed_tools
            ]
            allowed = {tool.name for tool in tools}
            for steps in range(1, self._max_agent_steps + 1):
                text: list[str] = []
                calls: list[ToolCallRequest] = []
                finished = False
                async with aclosing(self._llm.stream_chat(messages, tools)) as stream:
                    async for event in stream:
                        if isinstance(event, TextDelta):
                            text.append(event.text)
                            yield events.Token(text=event.text)
                        elif isinstance(event, ToolCallRequest):
                            arguments = deepcopy(event.arguments)
                            if event.name in allowed and event.name != "get_server_info":
                                arguments["repo"] = repo
                            call = ToolCallRequest(event.id, event.name, arguments)
                            calls.append(call)
                            yield events.ToolCall(
                                id=call.id, name=call.name, arguments=deepcopy(arguments)
                            )
                        elif isinstance(event, Done):
                            finished = True
                if not finished:
                    raise LLMError("The LLM stream ended before completion")
                if not calls:
                    yield events.Done(reason="completed", steps=steps)
                    return

                messages.append(
                    {
                        "role": "assistant",
                        "content": "".join(text) or None,
                        "tool_calls": [
                            {
                                "id": call.id,
                                "type": "function",
                                "function": {
                                    "name": call.name,
                                    "arguments": json.dumps(call.arguments),
                                },
                            }
                            for call in calls
                        ],
                    }
                )
                for call in calls:
                    result, change, warnings = await self._execute(call, allowed)
                    content = result.text
                    if not content and result.structured is not None:
                        content = json.dumps(result.structured)
                    if result.is_error:
                        content = "Tool error: " + (content or "The tool failed without a message")
                    messages.append({"role": "tool", "tool_call_id": call.id, "content": content})
                    yield events.ToolResult(
                        id=call.id,
                        name=call.name,
                        text=content,
                        structured=deepcopy(result.structured),
                        is_error=result.is_error,
                    )
                    for warning in warnings:
                        yield warning
                    if change is not None:
                        yield events.ChangeProposed(change=change)

            yield events.Warning(
                code="max_agent_steps",
                message=f"Agent stopped after reaching max_agent_steps ({self._max_agent_steps})",
            )
            yield events.Done(reason="max_steps", steps=steps)
        except DocterError as exc:
            yield events.Error(code=exc.code, message=exc.message)
            yield events.Done(reason="error", steps=steps)

    async def _execute(
        self, call: ToolCallRequest, allowed: set[str]
    ) -> tuple[ToolResult, ProposePatchOutput | None, list[events.Warning]]:
        if call.name not in allowed:
            return (
                ToolResult(
                    text=f"Tool '{call.name}' is not allowed or unavailable in this mode",
                    is_error=True,
                ),
                None,
                [],
            )
        try:
            result = await self._mcp.call_tool(
                call.name, call.arguments, timeout=Settings.DEFAULT_REQUEST_TIMEOUT_S
            )
        except MCPError as exc:
            return ToolResult(text=exc.message, is_error=True), None, []
        if result.is_error or call.name not in {"propose_patch", "translate_file"}:
            return result, None, []
        output_model = TranslateFileOutput if call.name == "translate_file" else ProposePatchOutput
        try:
            output = (
                output_model.model_validate(result.structured)
                if result.structured is not None
                else output_model.model_validate_json(result.text)
            )
        except ValidationError:
            return (
                ToolResult(
                    text=f"{call.name} returned an invalid {output_model.__name__}",
                    is_error=True,
                ),
                None,
                [],
            )
        warnings: list[events.Warning] = []
        if isinstance(output, TranslateFileOutput):
            change = ProposePatchOutput(
                file_path=output.target_path,
                patch=output.patch,
                rationale=output.rationale,
                is_new_file=output.is_new_file,
            )
            warnings = [
                events.Warning(code="translation_warning", message=message)
                for message in output.warnings
            ]
        else:
            change = output
        accepted = await self._change_sink.add(change)
        if isinstance(accepted, SinkError):
            return ToolResult(text=accepted.message, is_error=True), None, warnings
        return result, change, warnings
