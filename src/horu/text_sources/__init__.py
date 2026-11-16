"""Text sources turn a captured image into Japanese text."""

from __future__ import annotations

from horu.text_sources.base import TextLine, TextSource


def create(name: str) -> TextSource:
    if name == "rapidocr":
        from horu.text_sources.rapidocr_source import RapidOcrSource

        return RapidOcrSource()
    if name == "manga_ocr":
        from horu.text_sources.manga_ocr_source import MangaOcrSource

        return MangaOcrSource()
    raise ValueError(f"Unknown OCR engine: {name!r}")


__all__ = ["TextLine", "TextSource", "create"]
