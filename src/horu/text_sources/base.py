from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from PIL import Image


@dataclass(frozen=True)
class TextLine:
    text: str
    box: tuple[int, int, int, int]  # left, top, right, bottom in image pixels
    score: float = 1.0


class TextSource(Protocol):
    name: str

    def read(self, image: Image.Image) -> list[TextLine]:
        """Return the lines of Japanese text found in `image`."""
        ...
