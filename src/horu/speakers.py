"""Speakers seen in each game: their English, and a fix for misread name tags.

Name tags are often drawn in a decorative style that OCR misreads now and then (in Persona 5
a highlighted character inside the tag turns 少女の声 into 少回の声 or 少の声). Clean reads
of a name, often from a backlog screen, are remembered per game, and a later read that's one
character off a remembered name is corrected to it, but only when that's safe:

- a dropped character (少の声 -> 少女の声) is corrected, since dropping a character from a
  name doesn't produce a different real name;
- a replaced character is corrected only when OCR itself was unsure, since names one
  character apart can be different people (少年の声 and 少女の声).

Each speaker's English is worked out once and cached, so repeats cost nothing.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

DEFAULT_PATH = Path("data/speakers.json")
# OCR confidence needed to remember a name, and below which a one-character substitution is
# treated as a misread.
CONFIDENT = 0.9
UNSURE = 0.85


class SpeakerBook:
    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        self.path = path
        self._lock = threading.Lock()
        try:
            self._data: dict[str, dict[str, dict]] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._data = {}

    def resolve(self, game: str, text: str, score: float = 1.0) -> str:
        """The speaker name to use for an OCR read: a remembered name if this read is a
        misread of one, otherwise the read itself (remembered if it was confident)."""
        known = self._data.get(game, {})
        if text not in known:
            match = self._misread_of(text, score, known)
            if match:
                return match
        if score >= CONFIDENT:
            with self._lock:
                entry = self._data.setdefault(game, {}).setdefault(text, {"en": None, "source": None})
                entry["seen"] = entry.get("seen", 0) + 1
            self._save()
        return text

    def english(self, game: str, text: str) -> str | None:
        return self._data.get(game, {}).get(text, {}).get("en")

    def set_english(self, game: str, text: str, english: str, source: str) -> None:
        with self._lock:
            entry = self._data.setdefault(game, {}).setdefault(text, {"seen": 0})
            entry.update(en=english, source=source)
        self._save()

    def _misread_of(self, text: str, score: float, known: dict) -> str | None:
        matches = set()
        for name, entry in known.items():
            if not entry.get("seen"):  # only names read confidently at least once
                continue
            if len(name) == len(text) + 1 and _drops_one(name, text):
                matches.add(name)
            elif score < UNSURE and len(name) == len(text) and sum(a != b for a, b in zip(name, text)) == 1:
                matches.add(name)
        # Only an unambiguous match counts.
        return matches.pop() if len(matches) == 1 and len(text) >= 2 else None

    def _save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)


def _drops_one(longer: str, shorter: str) -> bool:
    """`shorter` is `longer` with exactly one character removed."""
    return any(longer[:i] + longer[i + 1 :] == shorter for i in range(len(longer)))
