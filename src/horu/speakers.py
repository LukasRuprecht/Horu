"""Speakers seen in each game: their English, and a fix for misread name tags.

Name tags are often drawn in a decorative style that OCR misreads now and then (in Persona 5
a highlighted character inside the tag turns 少女の声 into 少回の声 or 少の声). Clean reads
of a name, often from a backlog screen, are remembered per game, and a later read that's one
character off a remembered name is corrected to it, but only when that's safe:

- a dropped character (少の声 -> 少女の声) is corrected, since dropping a character from a
  name doesn't produce a different real name;
- a replaced character is corrected only when OCR itself was unsure, since names one
  character apart can be different people (少年の声 and 少女の声).

With an AI that reads images, each new name is read again from the screenshot once, and the
name it settles on is "confirmed": confirmed names are matched first, and a misread the AI
corrected (初の男性客 for 初老の男性客) maps straight to the right name from then on.

Decorated tags are misread in many ways (佐倉惣治郎 as 佐唐超治郎, 佐官惣治郎, 佐惣治郎, ...),
too many to check each with the AI. `guess` matches an unsure read that's close to one
confirmed name to it; the page offers to fix a wrong guess.

Each speaker's English is worked out once and cached, so repeats cost nothing.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from horu.text_sources.vote import edit_distance

DEFAULT_PATH = Path("data/speakers.json")
# OCR confidence needed to remember a name, and below which a one-character substitution is
# treated as a misread.
CONFIDENT = 0.9
UNSURE = 0.85
# Share of a confirmed name's characters a read may get wrong and still be guessed as it.
_GUESS_SLACK = 0.4
_SPACES = re.compile(r"[ \u3000]+")


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
        if known.get(text, {}).get("misread_of"):
            return known[text]["misread_of"]
        if not known.get(text, {}).get("confirmed"):
            # A confirmed name beats an unconfirmed read that's one character off it.
            confirmed = {name: entry for name, entry in known.items() if entry.get("confirmed")}
            match = self._misread_of(text, score, confirmed) or (None if text in known else self._misread_of(text, score, known))
            if match:
                return match
        if score >= CONFIDENT:
            with self._lock:
                entry = self._data.setdefault(game, {}).setdefault(text, {"en": None, "source": None})
                entry["seen"] = entry.get("seen", 0) + 1
            self._save()
        return text

    def guess(self, game: str, text: str, score: float, alternatives: tuple[str, ...] = ()) -> str | None:
        """A confirmed name this read is most likely a misread of, or None. Only when one
        confirmed name fits: the read (ignoring spaces) or another OCR model's reading is
        that name, or the read is close to it and OCR was unsure or dropped characters. A
        confident read the same length as a name but different (真代 for 貞代, or 少年の声
        beside 少女の声) may be another speaker, so it's left for the AI to check, and so may a
        read that's part of a name (貞代 in 川上貞代): a tag can show a shorter name."""
        if self.confirmed(game, text):
            return None
        confirmed = [
            name for name, entry in self._data.get(game, {}).items()
            if entry.get("confirmed") and not entry.get("misread_of") and name != text
        ]
        if not confirmed:
            return None
        read = _SPACES.sub("", text)
        exact = {name for name in confirmed if _SPACES.sub("", name) in {read, *(_SPACES.sub("", a) for a in alternatives)}}
        if exact:
            return exact.pop() if len(exact) == 1 else None
        if score >= CONFIDENT and not any(len(read) < len(_SPACES.sub("", n)) for n in confirmed):
            return None
        close = []
        for name in confirmed:
            plain = _SPACES.sub("", name)
            if len(plain) < 2 or (score >= CONFIDENT and len(read) >= len(plain)):
                continue
            distance = edit_distance(read, plain)
            if distance > 1 and read in plain:
                continue
            if distance <= max(1, round(_GUESS_SLACK * len(plain))):
                close.append((distance, name))
        close.sort()
        if not close or (len(close) > 1 and close[1][0] == close[0][0]):
            return None
        return close[0][1]

    def confirm(self, game: str, text: str, was: str | None = None) -> None:
        """The AI read the name tag as `text`: known from now on, so later misreads of it are
        matched to it. `was` is what OCR read, when that was a misread."""
        with self._lock:
            names = self._data.setdefault(game, {})
            entry = names.setdefault(text, {"en": None, "source": None})
            entry["seen"] = entry.get("seen", 0) + 1
            entry["confirmed"] = entry.get("confirmed", 0) + 1
            entry.pop("misread_of", None)
            if was and was != text:
                names.setdefault(was, {"en": None, "source": None, "seen": 0})["misread_of"] = text
        self._save()

    def confirmed(self, game: str, text: str) -> bool:
        return bool(self._data.get(game, {}).get(text, {}).get("confirmed"))

    def known(self, game: str) -> frozenset[str]:
        """Names read confidently at least once in this game."""
        return frozenset(
            name for name, entry in self._data.get(game, {}).items() if entry.get("seen") and not entry.get("misread_of")
        )

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
            if not entry.get("seen") or entry.get("misread_of"):  # only names read confidently at least once
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
