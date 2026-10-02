"""Local OCR with manga-ocr (https://github.com/kha-white/manga-ocr)."""

from __future__ import annotations

from PIL import Image


class MangaOcrSource:
    name = "manga_ocr"

    def __init__(self) -> None:
        # Imported lazily: loading torch and the model takes several seconds.
        from manga_ocr import MangaOcr

        self._ocr = MangaOcr()

    def read(self, image: Image.Image) -> str:
        return self._ocr(image)
