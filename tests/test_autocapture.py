from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from horu import autocapture
from horu.autocapture import ChangeWatcher, DialogueBoxes, Region

SIZE = (2560, 1369)
FIXTURES = Path(__file__).parent / "fixtures"


def test_a_region_is_suggested_once_captures_agree(tmp_path):
    boxes = DialogueBoxes(tmp_path / "boxes.json")
    assert boxes.observe("zelda", (633, 986, 1914, 1249), 3, SIZE) is None
    assert boxes.observe("zelda", (900, 300, 1200, 340), 1, SIZE) is None  # a one-off elsewhere (a sign)
    assert boxes.observe("zelda", (650, 985, 1910, 1252), 3, SIZE) is None
    suggested = boxes.observe("zelda", (727, 923, 1789, 1315), 3, SIZE)
    left, top, right, bottom = autocapture.to_pixels(suggested, (0, 0, *SIZE))
    # Covers every agreeing block, with room above for a name tag; the sign is left out.
    assert left < 633 and top < 923 and right > 1914 and bottom >= 1315 and top > 340
    # Only suggested: nothing is watched until the player saves a region, and it's suggested once.
    assert boxes.regions("zelda", SIZE) == []
    assert boxes.observe("zelda", (650, 985, 1910, 1252), 3, SIZE) is None


def test_regions_are_kept_per_game_and_screen_layout(tmp_path):
    boxes = DialogueBoxes(tmp_path / "boxes.json")
    one_screen, two_screens = (400, 240), (860, 287)
    regions = [Region("Dialogue", (0.1, 0.6, 0.9, 0.95)), Region("Chat", (0.6, 0.05, 0.98, 0.4))]
    boxes.set_regions("p5r", one_screen, regions)
    # The same layout at another window size shares them; another layout doesn't.
    assert boxes.regions("p5r", (800, 480)) == regions
    assert boxes.regions("p5r", two_screens) == []
    assert boxes.regions("zelda", one_screen) == []
    # Remembered across restarts.
    assert DialogueBoxes(tmp_path / "boxes.json").regions("p5r", one_screen) == regions
    # A layout with regions isn't suggested more.
    for _ in range(3):
        assert boxes.observe("p5r", (40, 150, 360, 220), 2, one_screen) is None


def frame(text: str, background: int = 40) -> Image.Image:
    image = Image.new("RGB", (900, 200), (background, background, background + 10))
    draw = ImageDraw.Draw(image)
    draw.text((40, 70), text, fill=(240, 240, 240), font=ImageFont.load_default(size=48))
    return image


def test_new_text_is_read_once_it_stops_changing():
    watcher = ChangeWatcher()
    # Typing out: still changing, so not read yet.
    assert not watcher.update(frame("Hel"))
    assert not watcher.update(frame("Hello th"))
    assert not watcher.update(frame("Hello there"))
    assert not watcher.update(frame("Hello there"))  # still for one frame
    assert watcher.update(frame("Hello there"))  # still for two: read it
    # The same text afterwards is not read again, even over a slightly different background.
    assert not any(watcher.update(frame("Hello there", background=44)) for _ in range(4))
    # The next line is.
    watcher.update(frame("General Kenobi"))
    watcher.update(frame("General Kenobi"))
    assert watcher.update(frame("General Kenobi"))


def test_scenery_moving_behind_the_box_is_not_new_text():
    # Ocarina of Time 3D: the owl flaps its wings behind a see-through box while the text
    # stays put. Watching the whole picture, the box never looked still and nothing was read.
    frames = [Image.open(FIXTURES / f"owl_box_{i:02d}.png") for i in range(4)]
    watcher = ChangeWatcher()
    reads = [watcher.update(frame) for frame in frames]
    assert reads == [False, False, True, False]


def emulator_window(width, height, picture):
    """An Azahar-like window: menu and status bars, black bars around a (dark) game picture,
    and the small touch screen beside it."""
    window = Image.new("RGB", (width, height), "black")
    draw = ImageDraw.Draw(window)
    draw.rectangle((0, 0, width, 30), fill=(32, 32, 32))  # menu bar
    draw.rectangle((0, height - 30, width, height), fill=(32, 32, 32))  # status bar
    left, top, right, bottom = picture
    draw.rectangle((left, top, right - 1, bottom - 1), fill=(20, 18, 16))  # a dark cave
    draw.rectangle((right, bottom - 100, width - 1, bottom - 1), fill=(90, 90, 90))  # touch screen
    return window


def test_the_game_picture_is_found_between_the_bars():
    assert autocapture.picture_box(emulator_window(880, 640, (0, 100, 733, 542))) == (0, 100, 733, 542)
    # No bars: the whole capture.
    assert autocapture.picture_box(Image.new("RGB", (400, 300), (40, 60, 80))) == (0, 0, 400, 300)


def test_a_region_follows_the_picture_when_the_window_is_resized(tmp_path):
    boxes = DialogueBoxes(tmp_path / "boxes.json")
    # A region saved on a capture from a wide window, watched in a narrow one.
    boxes.set_regions("zelda", (733, 442), [Region("Dialogue", (0.2, 0.65, 0.8, 0.9))])
    picture = (0, 162, 531, 481)
    (region,) = boxes.regions("zelda", (531, 319))
    left, top, right, bottom = autocapture.to_pixels(region.box, picture)
    assert (left, top, right, bottom) == (106, 369, 425, 449)


def test_boxes_from_before_regions_are_dropped(tmp_path):
    (tmp_path / "boxes.json").write_text('{"azahar.exe": {"frame": "picture", "seen": [{"box": [0.2, 0.6, 0.6, 0.8], "line": 0.06}]}}')
    assert DialogueBoxes(tmp_path / "boxes.json").regions("azahar.exe", (400, 240)) == []
