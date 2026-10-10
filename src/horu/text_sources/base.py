from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from PIL import Image


@dataclass(frozen=True)
class TextLine:
    text: str
    box: tuple[int, int, int, int]  # left, top, right, bottom in image pixels
    score: float = 1.0
    # Other OCR models' readings of the line, where they disagreed with `text`.
    alternatives: tuple[str, ...] = ()


class TextSource(Protocol):
    name: str

    def read(self, image: Image.Image) -> list[TextLine]:
        """Return the lines of Japanese text found in `image`."""
        ...
