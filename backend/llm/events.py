"""Typed gateway events and non-streaming completion results."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TextDelta:
    """A fragment of assistant text."""

    text: str


@dataclass(frozen=True)
class ToolCallRequest:
    """A complete tool invocation with decoded object arguments."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Usage:
    """Provider token counts and estimated USD cost; None means unavailable."""

    prompt_tokens: int | None
    completion_tokens: int | None
    cost_usd: float | None


@dataclass(frozen=True)
class Done:
    """The stream completed successfully."""


type ChatEvent = TextDelta | ToolCallRequest | Usage | Done


@dataclass(frozen=True)
class Completion:
    """A non-streaming text completion with its provider metadata."""

    text: str
    model: str
    stop_reason: str
    usage: Usage
