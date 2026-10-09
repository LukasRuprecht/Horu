"""Regression tests on real game screens.

Each fixture holds a screen's OCR'd lines (text, box, measured text style) and the blocks
Horu should produce: who is speaking, and what is dialogue versus other text. The
screenshots themselves aren't committed; only the extracted lines are.
"""

import json
from pathlib import Path

import pytest

from horu.layout import Style, group_blocks
from horu.text_sources.base import TextLine

SCREENS = [
    screen
    for fixture in sorted((Path(__file__).parent / "fixtures").glob("*.json"))
    for screen in json.loads(fixture.read_text(encoding="utf-8"))["screens"]
]


@pytest.mark.parametrize("screen", SCREENS, ids=[s["name"] for s in SCREENS])
def test_game_screen(screen):
    lines = [TextLine(l["text"], tuple(l["box"])) for l in screen["lines"]]
    styles = [None if l["style"] is None else Style(tuple(l["style"]["ink"]), l["style"]["light_on_dark"]) for l in screen["lines"]]
    blocks = group_blocks(lines, styles=styles)
    assert [{"speaker": b.speaker, "kind": b.kind, "text": b.text} for b in blocks] == screen["expected"]


def test_speakers_are_found_without_text_styles():
    """Name patterns and size still find the speaker when styles can't be measured."""
    log = next(s for s in SCREENS if s["name"] == "log_view_highlighted_entry")
    lines = [TextLine(l["text"], tuple(l["box"])) for l in log["lines"]]
    speakers = [b.speaker for b in group_blocks(lines) if b.speaker]
    assert speakers == ["少女の声", "少女の声", "少年のような声"]
