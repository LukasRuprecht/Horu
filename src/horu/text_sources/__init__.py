"""Text sources turn a captured image (or a game hook) into Japanese text."""

from __future__ import annotations

from horu.text_sources.base import TextSource


def create(name: str) -> TextSource:
    if name == "manga_ocr":
        from horu.text_sources.manga_ocr_source import MangaOcrSource

        return MangaOcrSource()
    raise ValueError(f"Unknown text source: {name!r}")


__all__ = ["TextSource", "create"]
