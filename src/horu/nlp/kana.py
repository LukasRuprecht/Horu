"""Kana helpers and furigana alignment."""

from __future__ import annotations

import re

_KANJI = r"㐀-䶿一-鿿豈-﫿々〆ヶ"
_KANJI_RUN = re.compile(rf"[{_KANJI}]+")
_RUNS = re.compile(rf"[{_KANJI}]+|[^{_KANJI}]+")

# A furigana segment: (text, reading). The reading is None when no furigana is needed.
Segment = tuple[str, str | None]


def to_hiragana(text: str) -> str:
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in text)


def has_kanji(text: str) -> bool:
    return bool(_KANJI_RUN.search(text))


def furigana(surface: str, reading: str) -> list[Segment]:
    """Split `surface` into segments so furigana sits only over the kanji.

    furigana("行った", "いった") -> [("行", "い"), ("った", None)]
    """
    reading = to_hiragana(reading)
    if not has_kanji(surface) or not reading:
        return [(surface, None)]
    runs = _RUNS.findall(surface)
    pattern = "".join("(.+?)" if _KANJI_RUN.fullmatch(r) else f"({re.escape(to_hiragana(r))})" for r in runs)
    match = re.fullmatch(pattern, reading)
    if match is None:
        # Reading and spelling don't line up (irregular or OCR-garbled): annotate the whole word.
        return [(surface, reading)]
    return [(run, part if _KANJI_RUN.fullmatch(run) else None) for run, part in zip(runs, match.groups())]
