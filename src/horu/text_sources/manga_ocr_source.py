"""Local OCR with manga-ocr (https://github.com/kha-white/manga-ocr).

manga-ocr reads a single text block and has no detector, so it only works with a capture
region drawn tightly around the text.
"""

from __future__ import annotations

from PIL import Image

from horu.preprocess import crop_to_text
from horu.text_sources.base import TextLine


class MangaOcrSource:
    name = "manga_ocr"

    def __init__(self) -> None:
        # Imported lazily: loading torch and the model takes several seconds.
        try:
            from manga_ocr import MangaOcr
        except ImportError as e:
            raise ImportError('manga-ocr is not installed. Run "uv sync --extra manga-ocr".') from e

        self._ocr = MangaOcr()

    def read(self, image: Image.Image) -> list[TextLine]:
        text = self._ocr(crop_to_text(image))
        return [TextLine(text, (0, 0, image.width, image.height))] if text else []
