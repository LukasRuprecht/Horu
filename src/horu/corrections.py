"""Corrections the user makes in the window ("this is dialogue", "this is a speaker name"),
remembered per game and applied to later captures.

Rules match a line's exact text (ignoring spaces), so fixing one misfiled button legend or
name tag fixes it everywhere in that game.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PATH = Path("data/corrections.json")
LABELS = ("dialogue", "other", "speaker", "not_speaker")
DEFAULT_GAME = "default"


@dataclass(frozen=True)
class Hints:
    """One game's corrections, in the form the layout step uses."""

    dialogue: frozenset[str] = field(default_factory=frozenset)
    other: frozenset[str] = field(default_factory=frozenset)
    speaker: frozenset[str] = field(default_factory=frozenset)
    not_speaker: frozenset[str] = field(default_factory=frozenset)
    # Not a correction: speaker names already read confidently in this game (from the
    # speaker book), which count as name-like.
    known_speakers: frozenset[str] = field(default_factory=frozenset)


def normalize(text: str) -> str:
    return text.replace(" ", "").replace("　", "").strip()


class Corrections:
    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        self.path = path
        self._lock = threading.Lock()
        try:
            self._data: dict[str, dict[str, str]] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._data = {}

    def hints(self, game: str) -> Hints:
        rules = self._data.get(game, {})
        by_label = {label: frozenset(t for t, l in rules.items() if l == label) for label in LABELS}
        return Hints(**by_label)

    def mark(self, game: str, text: str, label: str | None) -> None:
        """Remember `label` for `text` in `game`; None forgets any rule for it."""
        if label is not None and label not in LABELS:
            raise ValueError(f"Unknown label {label!r}")
        key = normalize(text)
        with self._lock:
            rules = self._data.setdefault(game, {})
            if label is None:
                rules.pop(key, None)
            else:
                rules[key] = label
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)
