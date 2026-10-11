from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from horu import autocapture
from horu.autocapture import ChangeWatcher, DialogueBoxes

SIZE = (2560, 1369)
FIXTURES = Path(__file__).parent / "fixtures"


def test_the_dialogue_box_is_learned_once_captures_agree(tmp_path):
    boxes = DialogueBoxes(tmp_path / "boxes.json")
    boxes.observe("zelda", (633, 986, 1914, 1249), 3, SIZE)
    boxes.observe("zelda", (900, 300, 1200, 340), 1, SIZE)  # a one-off elsewhere (a sign)
    boxes.observe("zelda", (650, 985, 1910, 1252), 3, SIZE)
    assert boxes.region("zelda") is None and boxes.progress("zelda") == (2, 3)
    boxes.observe("zelda", (727, 923, 1789, 1315), 3, SIZE)
    left, top, right, bottom = autocapture.to_pixels(boxes.region("zelda"), SIZE)
    # Covers every agreeing block, with room above for a name tag; the sign is left out.
    assert left < 633 and top < 923 and right > 1914 and bottom >= 1315 and top > 340
    # Remembered across restarts.
    assert DialogueBoxes(tmp_path / "boxes.json").region("zelda") == boxes.region("zelda")


def test_a_box_set_by_hand_is_used_at_once(tmp_path):
    boxes = DialogueBoxes(tmp_path / "boxes.json")
    boxes.set("p5r", (370, 1119, 1361, 1289), 2, SIZE)
    assert boxes.region("p5r") is not None and boxes.progress("p5r") == (3, 3)


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
