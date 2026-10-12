import json
import zipfile

from PIL import Image, ImageDraw

from horu import debug_bundle
import test_game_screens
from horu.corrections import Hints
from horu.text_sources.base import TextLine


def _capture():
    image = Image.new("RGB", (400, 200), (20, 20, 30))
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 120, 200, 140), fill=(240, 240, 240))  # stands in for a line of text
    lines = [TextLine("少女の声", (40, 90, 120, 110)), TextLine("ん…?何だ?", (40, 120, 200, 140), 0.87)]
    shown = [{"kind": "dialogue", "text": "ん…?何だ?", "repeat": False, "uncertain": True, "alternatives": [],
              "speaker": {"text": "少女の声"}}]
    return debug_bundle.Capture(image, lines, "P5R.exe", False, Hints(speaker=frozenset({"少女の声"})), shown)


def test_bundle_holds_screenshot_screen_and_report(tmp_path):
    path = debug_bundle.save(_capture(), "name tag missing", {"ocr_engine": "rapidocr"}, folder=tmp_path)
    assert path.parent == tmp_path and path.name.startswith("horu-debug-") and path.name.endswith("-P5R.zip")
    with zipfile.ZipFile(path) as z:
        assert set(z.namelist()) == {"README.txt", "capture.png", "screen.json", "report.json"}
        screen = json.loads(z.read("screen.json"))
        report = json.loads(z.read("report.json"))
    assert screen["size"] == [400, 200]
    assert [l["text"] for l in screen["lines"]] == ["少女の声", "ん…?何だ?"]
    assert "score" not in screen["lines"][0] and screen["lines"][1]["score"] == 0.87
    assert all({"speaker", "kind", "text"} == set(b) for b in screen["expected"])
    assert report["note"] == "name tag missing"
    assert report["shown"][0]["speaker"] == "少女の声"
    assert report["corrections"]["speaker"] == ["少女の声"]
    assert report["settings"] == {"ocr_engine": "rapidocr"}


def test_bundle_screen_runs_as_a_regression_screen(tmp_path):
    """add_to_fixture appends the screen in the form tests/test_game_screens.py reads."""
    fixture = tmp_path / "games.json"
    fixture.write_text(json.dumps({"about": "test", "screens": []}), encoding="utf-8")
    bundle = debug_bundle.save(_capture(), folder=tmp_path)
    debug_bundle.add_to_fixture(bundle, fixture, "girl_voice")
    screens = json.loads(fixture.read_text(encoding="utf-8"))["screens"]
    assert [s["name"] for s in screens] == ["girl_voice"]
    test_game_screens.test_game_screen(screens[0])
