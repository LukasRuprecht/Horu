"""Turning detected lines into readable blocks of Japanese text."""

from __future__ import annotations

import re
from dataclasses import dataclass

from horu.text_sources.base import TextLine

# Hiragana, katakana, CJK ideographs (kanji), and the halfwidth katakana range.
_JAPANESE = re.compile(r"[぀-ヿ㐀-䶿一-鿿ｦ-ﾟ]")
_SENTENCE_PUNCTUATION = re.compile(r"[。、！？!?…‥「」『』]")
_MAX_SPEAKER_CHARS = 8
_MIN_BLOCK_CHARS = 2


@dataclass(frozen=True)
class TextBlock:
    lines: tuple[TextLine, ...]
    speaker: str | None = None

    @property
    def text(self) -> str:
        # Japanese has no spaces between words, so wrapped lines join directly.
        return "".join(line.text for line in self.lines)

    @property
    def box(self) -> tuple[int, int, int, int]:
        return (
            min(l.box[0] for l in self.lines),
            min(l.box[1] for l in self.lines),
            max(l.box[2] for l in self.lines),
            max(l.box[3] for l in self.lines),
        )


def is_japanese(text: str) -> bool:
    return bool(_JAPANESE.search(text))


def group_blocks(lines: list[TextLine]) -> list[TextBlock]:
    """Group Japanese lines into blocks (e.g. one dialogue box), in reading order.

    Two lines belong together when they have a similar text height, the second starts just
    below the first, and they overlap horizontally. A short first line without punctuation
    above longer text (a name tag) becomes the block's speaker.
    """
    groups: list[list[TextLine]] = []
    for line in sorted((l for l in lines if is_japanese(l.text)), key=lambda l: l.box[1]):
        for group in groups:
            if _continues(group[-1], line):
                group.append(line)
                break
        else:
            groups.append([line])
    # Lone characters are almost always icons or decorations misread as kanji.
    return [_to_block(g) for g in groups if sum(len(l.text) for l in g) >= _MIN_BLOCK_CHARS]


def _to_block(lines: list[TextLine]) -> TextBlock:
    first = lines[0]
    if len(lines) > 1 and _looks_like_name(first.text) and len(lines[1].text) > len(first.text):
        return TextBlock(tuple(lines[1:]), speaker=first.text)
    return TextBlock(tuple(lines))


def _looks_like_name(text: str) -> bool:
    return len(text) <= _MAX_SPEAKER_CHARS and not _SENTENCE_PUNCTUATION.search(text)


def _continues(prev: TextLine, line: TextLine) -> bool:
    p_left, p_top, p_right, p_bottom = prev.box
    left, top, right, bottom = line.box
    p_height, height = p_bottom - p_top, bottom - top
    similar_size = max(p_height, height) <= 1.5 * min(p_height, height)
    close_below = -0.3 * p_height <= top - p_bottom <= 0.9 * p_height
    overlaps = min(p_right, right) > max(p_left, left)
    return similar_size and close_below and overlaps
