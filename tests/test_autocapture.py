from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from horu import autocapture
from horu.autocapture import DialogueBoxes, Region, TextWatcher
from horu.text_sources.rapidocr_source import text_finder

FIXTURES = Path(__file__).parent / "fixtures"


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


@pytest.fixture(scope="module")
def find_text():
    return text_finder(autocapture.MAP_WIDTH)


def screen(name: str) -> Image.Image:
    return Image.open(FIXTURES / f"{name}.png").convert("RGB")


def fired(watcher: TextWatcher, frames: list[Image.Image], regions=None) -> list[int]:
    return [i for i, frame in enumerate(frames) if watcher.update(frame, regions)]


def test_new_text_is_read_once_it_has_stayed_put(find_text):
    # P5R: a line, then choices appear under it. Each is read once, after two still frames.
    line, choices = screen("p5r_watch_line"), screen("p5r_watch_choices")
    assert fired(TextWatcher(find_text), [line] * 3 + [choices] * 6) == [2, 5]
    # A thought bubble popping up over the street is new text too.
    street, bubble = screen("p5r_watch_street"), screen("p5r_watch_thought_bubble")
    assert fired(TextWatcher(find_text), [street] * 3 + [bubble] * 6) == [2, 5]


def test_text_that_stays_the_same_is_not_read_again(find_text):
    assert fired(TextWatcher(find_text), [screen("p5r_watch_sign")] * 20) == [2]


def shifted(image: Image.Image, dx: int) -> Image.Image:
    out = Image.new("RGB", image.size)
    out.paste(image, (dx, 0))
    return out


def test_a_sign_moving_with_the_camera_is_not_read_until_it_stops(find_text):
    sign = screen("p5r_watch_sign")
    panning = [shifted(sign, 3 * i) for i in range(10)]
    assert fired(TextWatcher(find_text), panning + [panning[-1]] * 5) == [11]


def test_scenery_moving_behind_the_box_is_not_new_text(find_text):
    # Ocarina of Time 3D: the owl flaps its wings behind a see-through box while the text
    # stays put.
    frames = [Image.open(FIXTURES / f"owl_box_{i:02d}.png") for i in range(4)]
    assert fired(TextWatcher(find_text), frames + frames[-1:] * 3) == [2]


def test_with_regions_only_text_inside_them_counts(find_text):
    line = screen("p5r_watch_line")
    # P5R's choices appear above the dialogue box, to the right: only the box is watched.
    w, h = line.size
    where = (round(w * 0.55), round(h * 0.4), round(w * 0.75), round(h * 0.76))
    choices = line.copy()
    choices.paste(screen("p5r_watch_choices").crop(where), where[:2])
    dialogue_box = [(0.0, 0.78, 0.8, 1.0)]
    assert fired(TextWatcher(find_text), [line] * 3 + [choices] * 6) == [2, 5]
    assert fired(TextWatcher(find_text), [line] * 3 + [choices] * 6, dialogue_box) == [2]


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


class Drawn:
    """Frames of a 320-wide dialogue box with lines of text drawn in it, and a stand-in
    detector that finds those lines."""

    def __init__(self):
        self.boxes = []
        self.font = ImageFont.load_default(size=16)

    def frame(self, lines: list[str], cover: tuple[int, int, int, int] | None = None) -> Image.Image:
        image = Image.new("RGB", (320, 180), (30, 40, 60))
        draw = ImageDraw.Draw(image)
        self.boxes = []
        for i, text in enumerate(lines):
            if text:
                draw.text((20, 100 + 22 * i), text, fill=(240, 240, 240), font=self.font)
                self.boxes.append(tuple(int(v) for v in draw.textbbox((20, 100 + 22 * i), text, font=self.font)))
        if cover:
            draw.ellipse(cover, fill=(200, 200, 255))  # a fairy flying over the text
        return image

    def detect(self, pixels):
        return list(self.boxes)


def test_a_box_is_read_once_it_has_finished_typing_out():
    # Ocarina of Time: the first lines settle while the last is still typing out; reading
    # then gave a line per character typed.
    drawn = Drawn()
    watcher = TextWatcher(drawn.detect)
    first, second = "Din's Fire obtained!", "Set it to a button"
    frames = [[first, second, "A dome"], [first, second, "A dome of"], [first, second, "A dome of fla"],
              [first, second, "A dome of flame!"], [first, second, "A dome of flame!"], [first, second, "A dome of flame!"]]
    assert [i for i, lines in enumerate(frames) if watcher.update(drawn.frame(lines))] == [5]


def test_text_coming_back_after_something_passed_over_it_is_not_new():
    drawn = Drawn()
    watcher = TextWatcher(drawn.detect)
    lines = ["Din's Fire obtained!", "A dome of flame!"]
    covered = [drawn.frame(lines, cover=(30, 95, 70, 125))] * 4  # Navi hovers over the first line
    reads = [watcher.update(f) for f in [drawn.frame(lines)] * 3 + covered + [drawn.frame(lines)] * 4]
    assert reads.count(True) <= 2 and not any(reads[7:])  # not again once she's gone


def cafe(name: str) -> Image.Image:
    return Image.open(FIXTURES / f"p5r_cafe_{name}.jpg").convert("RGB")


def test_new_lines_are_read_though_the_name_tag_beside_them_keeps_changing(find_text):
    # P5R, Leblanc: the tilted name tag's box takes in the scene moving behind it, so it never
    # settles; the line under it was then never read. Frames of a screen alternate as the
    # scene moves.
    watcher = TextWatcher(find_text)
    fired(watcher, [cafe("before")] * 4)
    assert fired(watcher, [cafe("who_1"), cafe("who_2")] * 6)  # なに、誰の話？
    # 別に。: three characters, missed by the text finder on a smaller picture.
    assert fired(watcher, [cafe("nothing_1"), cafe("nothing_2")] * 6)
