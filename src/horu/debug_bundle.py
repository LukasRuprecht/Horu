"""Debug bundles: one capture with everything Horu made of it, zipped for a bug report.

A bundle holds the screenshot, the OCR's lines with their measured text styles, and the
blocks the layout step made of them, in the same form as the regression screens in
tests/fixtures/*_screens.json. Fixing a reported screen is then: correct the bundle's
expected blocks, and add it to a fixture (`add_to_fixture`).
"""

from __future__ import annotations

import dataclasses
import io
import json
import platform
import sys
import time
import zipfile
from pathlib import Path

from PIL import Image

from horu import __version__, layout
from horu.corrections import Hints
from horu.text_sources.base import TextLine

DEFAULT_DIR = Path("debug")

_README = """Horu debug bundle
=================

What's inside:
- capture.png: the screenshot Horu read. It shows whatever was on screen, so look at it
  before sharing the bundle.
- screen.json: the text OCR found (with positions and text colours) and how Horu grouped it
  into dialogue, speakers, and other text. Same format as Horu's regression screens.
- report.json: what Horu showed, your note, the corrections and speaker names it used for
  this game, and versions. No API keys or other secrets.

To report a problem, attach this zip to an issue at
https://github.com/LukasRuprecht/Horu/issues and say what you expected to see.
"""


@dataclasses.dataclass
class Capture:
    """A capture as Horu read it, kept for a while in case it's reported."""

    image: Image.Image
    lines: list[TextLine]  # what OCR read
    game: str
    auto: bool
    hints: Hints
    shown: list[dict] = dataclasses.field(default_factory=list)  # lines sent to the page
    time: float = dataclasses.field(default_factory=time.time)


def screen(capture: Capture, name: str = "reported_screen") -> dict:
    """The capture as a regression screen: OCR lines with styles, and today's blocks as the
    expected result (to be checked by hand). Blocks are made without the game's corrections,
    as the regression tests do."""
    styles = [layout.line_style(capture.image, line.box) for line in capture.lines]
    lines = []
    for line, style in zip(capture.lines, styles):
        entry = {
            "text": line.text,
            "box": list(line.box),
            "style": None if style is None else {"ink": [round(c) for c in style.ink], "light_on_dark": style.light_on_dark},
        }
        if line.score < 1.0:
            entry["score"] = round(line.score, 3)
        if line.slant_height:
            entry["slant_height"] = round(line.slant_height, 1)
        lines.append(entry)
    blocks = layout.group_blocks(capture.lines, styles=styles)
    return {
        "name": name,
        "size": list(capture.image.size),
        "lines": lines,
        "expected": [{"speaker": b.speaker, "kind": b.kind, "text": b.text} for b in blocks],
    }


def report(capture: Capture, note: str, settings: dict) -> dict:
    return {
        "note": note,
        "game": capture.game,
        "captured": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(capture.time)),
        "auto_capture": capture.auto,
        "shown": [
            {k: line.get(k) for k in ("kind", "text", "repeat", "uncertain", "alternatives")}
            | {"speaker": line["speaker"]["text"] if line.get("speaker") else None}
            for line in capture.shown
        ],
        "corrections": {k: sorted(v) for k, v in dataclasses.asdict(capture.hints).items()},
        "settings": settings,
        "versions": {"horu": __version__, "python": sys.version.split()[0], "os": platform.platform()},
    }


def save(capture: Capture, note: str = "", settings: dict | None = None, folder: Path | None = None) -> Path:
    """Write the bundle as a zip in `folder` and return its path."""
    folder = folder or DEFAULT_DIR
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(capture.time))
    game = "".join(c if c.isalnum() or c in "-_" else "_" for c in Path(capture.game).stem) or "game"
    path = folder / f"horu-debug-{stamp}-{game}.zip"
    png = io.BytesIO()
    capture.image.convert("RGB").save(png, format="PNG")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", _README)
        z.writestr("capture.png", png.getvalue())
        z.writestr("screen.json", _json(screen(capture)))
        z.writestr("report.json", _json(report(capture, note, settings or {})))
    return path


def add_to_fixture(bundle: Path, fixture: Path, name: str) -> dict:
    """Add a bundle's screen to a regression fixture under `name`, and return it. Its expected
    blocks are what Horu made at the time: correct them in the fixture if they were wrong."""
    with zipfile.ZipFile(bundle) as z:
        entry = json.loads(z.read("screen.json"))
    entry["name"] = name
    data = json.loads(fixture.read_text(encoding="utf-8"))
    if any(s["name"] == name for s in data["screens"]):
        raise ValueError(f"{fixture} already has a screen named {name!r}")
    data["screens"].append(entry)
    fixture.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return entry


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=1)
