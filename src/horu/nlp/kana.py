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


# The vowel each hiragana ends in, for spelling out a stretched ー.
_VOWEL_ROWS = {
    "あ": "あかさたなはまやらわがざだばぱぁゃゎ",
    "い": "いきしちにひみりぎじぢびぴぃ",
    "う": "うくすつぬふむゆるぐずづぶぷぅゅ",
    "え": "えけせてねへめれげぜでべぺぇ",
    "お": "おこそとのほもよろをごぞどぼぽぉょ",
}
_VOWEL = {kana: vowel for vowel, row in _VOWEL_ROWS.items() for kana in row}
# A long o is usually written う (ありがとう, そう), a long e sometimes い (せんせい).
_LONG = {"お": "うお", "え": "えい"}


def unstretched(text: str) -> list[str]:
    """Dictionary spellings of casual text with ー after hiragana: ねー -> ねえ, そー -> そう,
    すごーい -> すごい. Empty when there is nothing to undo."""
    if not re.search(r"[ぁ-ゖ]ー", text):
        return []  # katakana words (コーヒー) are spelled with ー
    variants = [""]
    for i, c in enumerate(text):
        vowel = _VOWEL.get(text[i - 1]) if c == "ー" and i else None
        if vowel is None:
            variants = [v + c for v in variants]
        else:
            variants = [v + long for v in variants for long in _LONG.get(vowel, vowel)]
    dropped = re.sub(r"(?<=[ぁ-ゖ])ー", "", text)
    return list(dict.fromkeys([*variants, dropped]))
