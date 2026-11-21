"""Serializable, discriminated events emitted by the agent loop."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from docter_contract import ProposePatchOutput


class _Event(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Token(_Event):
    """A streamed fragment of assistant text."""

    type: Literal["token"] = "token"
    text: str


class ToolCall(_Event):
    """A requested tool invocation, including any project-bound arguments."""

    type: Literal["tool_call"] = "tool_call"
    id: str
    name: str
    arguments: dict[str, Any]


class ToolResult(_Event):
    """The tool response fed back to the LLM, including sink rejections."""

    type: Literal["tool_result"] = "tool_result"
    id: str
    name: str
    text: str
    structured: Any = None
    is_error: bool = False


class ChangeProposed(_Event):
    """A patch successfully accepted by the change sink for user review."""

    type: Literal["change_proposed"] = "change_proposed"
    change: ProposePatchOutput
    change_id: int | None = None


class Warning(_Event):
    """A non-fatal limit or condition that needs user attention."""

    type: Literal["warning"] = "warning"
    code: str
    message: str


class Error(_Event):
    """A terminal domain error; tool failures instead produce ToolResult."""

    type: Literal["error"] = "error"
    code: str
    message: str


class Done(_Event):
    """The run ended; steps counts attempted LLM completions."""

    type: Literal["done"] = "done"
    reason: Literal["completed", "max_steps", "error"]
    steps: int


type AgentEvent = Annotated[
    Token | ToolCall | ToolResult | ChangeProposed | Warning | Error | Done,
    Field(discriminator="type"),
]
