import dataclasses
import io
from pathlib import Path

import pytest
from PIL import Image

from horu import app as horu_app, config
from horu.wordbook import Wordbook

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def app(tmp_path, monkeypatch):
    cfg = dataclasses.replace(config.load(ROOT / "config.example.toml"), save_captures=False)
    monkeypatch.chdir(tmp_path)
    return horu_app.App(cfg)


def game_window() -> Image.Image:
    """A window: an emulator's black bars around a 1600x900 game picture."""
    window = Image.new("RGB", (2000, 1100), "black")
    window.paste(Image.new("RGB", (1600, 900), (90, 120, 180)), (200, 100))
    return window


def test_flashcards_get_the_whole_scene_and_ai_checks_the_line(app):
    window = game_window()
    app._keep_screenshot(1, window, (400, 800, 1400, 900))
    app._keep_scene([1, 2], window, (200, 100, 1800, 1000))
    scene = Image.open(io.BytesIO(app.scene_for(1)))
    assert scene.format == "JPEG" and scene.size == (1280, 720)  # the game's picture, no bars, kept no wider than 1280
    assert app.scene_for(2) is app.scene_for(1)  # lines read together share one picture
    line = Image.open(io.BytesIO(app._screenshots[1]))
    assert line.format == "PNG" and line.height < 300  # what the AI checks the reading against: the line itself


def test_a_line_whose_scene_is_gone_falls_back_to_its_crop(app):
    app._screenshots[7] = b"png crop"
    assert app.scene_for(7) == b"png crop" and app.scene_for(8) is None
    for n in range(horu_app._SCENES_KEPT + 5):
        app._keep_scene([100 + n], game_window(), None)
    assert 100 not in app._scenes and len(app._scenes) == horu_app._SCENES_KEPT


def test_saved_words_keep_a_jpeg_scene_and_older_png_crops(tmp_path):
    book = Wordbook(tmp_path / "words.db")
    jpeg = io.BytesIO()
    Image.new("RGB", (1280, 720), "blue").save(jpeg, "JPEG")
    scene_word, _ = book.add(word="入る", reading="はいる", screenshot_png=jpeg.getvalue())
    crop_word, _ = book.add(word="駅", reading="えき", screenshot_png=b"\x89PNG old crop")
    assert book.image(scene_word["id"]) == jpeg.getvalue() and (tmp_path / "word_images" / f"{scene_word['id']}.jpg").exists()
    assert book.image(crop_word["id"]) == b"\x89PNG old crop"
    book.remove(scene_word["id"])
    assert book.image(scene_word["id"]) is None


def test_a_dark_scene_keeps_its_dim_edges_but_bars_are_cut():
    from horu.autocapture import scene_box

    # Black bars either side of a picture: cut.
    assert scene_box(game_window()) == (200, 100, 1800, 1000)
    # A dark dungeon: the middle lit, the edges dim but not black. picture_box would take the
    # dim edges for bars; the scene keeps them.
    dungeon = Image.new("RGB", (1600, 900), (6, 6, 8))
    dungeon.paste(Image.new("RGB", (600, 900), (70, 60, 50)), (500, 0))
    left, top, right, bottom = scene_box(dungeon)
    assert (left, right) == (0, 1600)
