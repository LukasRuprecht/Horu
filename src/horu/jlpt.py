"""JLPT levels for words, from the open JLPT vocabulary lists (see resources/jlpt.tsv)."""

from __future__ import annotations

from functools import cache
from pathlib import Path

TABLE = Path(__file__).parent / "resources" / "jlpt.tsv"


@cache
def _levels() -> tuple[dict[tuple[str, str], int], dict[str, int]]:
    """(word, reading) -> level, and word -> its easiest level (a word can have readings at
    several levels)."""
    exact: dict[tuple[str, str], int] = {}
    by_word: dict[str, int] = {}
    for row in TABLE.read_text(encoding="utf-8").splitlines():
        if not row or row.startswith("#"):
            continue
        word, reading, level = row.split("\t")
        exact[(word, reading)] = int(level)
        for key in {word, reading}:
            by_word[key] = max(by_word.get(key, 0), int(level))
    return exact, by_word


def level(word: str, reading: str = "") -> int | None:
    """The word's JLPT level, 5 (N5, easiest) to 1 (N1), or None when it isn't on the lists.
    `word` is the dictionary form; a word written in kana is also found by its reading."""
    exact, by_word = _levels()
    found = exact.get((word, _hiragana(reading))) or by_word.get(word)
    if found is None and reading and word == reading:
        found = by_word.get(_hiragana(reading))
    return found


def _hiragana(text: str) -> str:
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in text)
