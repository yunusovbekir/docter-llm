"""Patch proposal storage, independent of persistence and review workflows."""

from dataclasses import dataclass
from typing import Protocol

from docter_contract import ProposePatchOutput


@dataclass(frozen=True)
class Ok:
    """The proposal was accepted for review, not applied to the repository."""

    change_id: int | None = None


@dataclass(frozen=True)
class Error:
    """The proposal was rejected; the message tells the LLM how to retry."""

    message: str


class ChangeSink(Protocol):
    """Accept proposals within the caller's project/session scope."""

    async def add(self, change: ProposePatchOutput) -> Ok | Error:
        """Store a proposal or return an actionable rejection message."""
        ...


class InMemoryChangeSink:
    """Collect accepted proposals without writing to disk."""

    def __init__(self) -> None:
        """Create an empty proposal collection."""
        self.changes: list[ProposePatchOutput] = []

    async def add(self, change: ProposePatchOutput) -> Ok:
        """Append a proposal and acknowledge its acceptance for review."""
        self.changes.append(change)
        return Ok()
