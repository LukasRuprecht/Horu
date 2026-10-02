from __future__ import annotations

from typing import Protocol

from PIL import Image


class TextSource(Protocol):
    name: str

    def read(self, image: Image.Image) -> str:
        """Return the Japanese text in `image`."""
        ...
