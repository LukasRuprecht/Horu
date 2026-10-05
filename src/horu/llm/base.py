from __future__ import annotations

from typing import TYPE_CHECKING, Iterator, Protocol

if TYPE_CHECKING:
    from horu.llm.tools import Tool


class AIError(Exception):
    """A failure to show the user as-is (bad key, provider unreachable, ...)."""


class Chat(Protocol):
    """One conversation. History is kept by the chat in its provider's own format."""

    def send(self, text: str) -> Iterator[str]:
        """Send a user message and yield the reply as text chunks.

        The exchange is added to the history only once the reply completes.
        """
        ...

    def cancel(self) -> None:
        """Stop the reply currently streaming, if any."""
        ...


class Provider(Protocol):
    label: str  # shown in the UI, e.g. "Claude (claude-opus-5-5)"

    def chat(
        self, system: str, effort: str = "low", history: list[dict] | None = None, temperature: float | None = None
    ) -> Chat:
        """Start a conversation.

        `history` pre-fills earlier turns ({"role", "content"} dicts with text content).
        `effort` and `temperature` are hints; providers that don't support one ignore it.
        """
        ...

    def run_tools(
        self, system: str, task: str, tools: list[Tool], effort: str = "low", max_turns: int = 12, follow_up=None
    ) -> str:
        """Work on `task`, calling `tools` as the model decides; returns its final answer.
        `follow_up` (see llm.tools.FollowUp) can send the model back to work once it stops."""
        ...
