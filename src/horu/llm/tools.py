"""Tools an AI model can call, shared by every provider's tool-calling loop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict  # JSON Schema for the input object
    run: Callable[[dict], str]  # takes the parsed input, returns the result as text

    def call(self, arguments: dict) -> tuple[str, bool]:
        """Run the tool; returns (result, is_error). Failures go back to the model as text,
        so it can recover instead of the whole task failing."""
        try:
            return self.run(arguments), False
        except Exception as e:  # noqa: BLE001 - any tool failure is reported to the model
            return f"Error: {e}", True


# Called with a short description of each step, for progress updates in the window.
OnStep = Callable[[str], None]
# Called with the model's answer when it stops calling tools. Returning a message sends it
# back as the next user turn so the model continues (e.g. to nudge a small model that
# described a tool call instead of making it); returning None accepts the answer.
FollowUp = Callable[[str], "str | None"]
