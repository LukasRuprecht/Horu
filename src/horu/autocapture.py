"""Auto-capture: read the game picture by itself whenever new text settles on it.

A few times a second the game picture is shrunk and a text detector finds where text is (not
what it says: that's cheap). Each piece of text is followed on its own; when one is new or
has changed, and has stayed put for a moment (finished typing out), the whole picture is read
like a hotkey capture. Only dialogue from that read is shown, so a sign the camera stops on
costs a read but shows nothing. See TextWatcher.

Dialogue isn't told apart by size: P5R's lines are the same height as its button hints. What
sets it apart is that it changes, while HUD text doesn't.

The player can limit watching to parts of the picture ("auto-read regions"), per game and per
screen layout, for a game whose HUD keeps changing text. Positions are kept relative to the
game picture, not the window: emulators centre the picture between black bars that change
with the window's size.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

DEFAULT_PATH = Path("data/dialogue_boxes.json")
_VERSION = 2  # earlier files held one learned box per game, from mixed layouts: dropped

# Room around text when suggesting a region from it, in line heights: the name tag sits above
# the text, and longer lines can reach further than the ones seen so far.
_PAD_TOP, _PAD_BOTTOM, _PAD_SIDES = 1.8, 0.6, 1.5

Box = tuple[float, float, float, float]  # left, top, right, bottom as fractions of the game picture
Pixels = tuple[int, int, int, int]


@dataclass(frozen=True)
class Observation:
    box: Box
    line_height: float  # as a fraction of the picture height


@dataclass(frozen=True)
class Region:
    name: str
    box: Box


def layout(size: tuple[int, int]) -> str:
    """A name for the screen layout of a game picture of `size` (width, height): its shape.
    One 3DS screen and two side by side are different layouts; resizing the window is not."""
    width, height = size
    return f"{width / max(height, 1):.1f}"


class DialogueBoxes:
    """Each game's auto-read regions, per screen layout."""

    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        self.path = path
        self._lock = threading.Lock()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        # game -> layout -> {"regions": [{"name", "box"}]}
        self._games: dict[str, dict[str, dict]] = data.get("games", {}) if data.get("version") == _VERSION else {}

    def regions(self, game: str, size: tuple[int, int]) -> list[Region]:
        """The regions to watch in a picture of `size`."""
        entry = self._games.get(game, {}).get(layout(size), {})
        return [Region(r["name"], tuple(r["box"])) for r in entry.get("regions", [])]

    def set_regions(self, game: str, size: tuple[int, int], regions: list[Region]) -> None:
        """Replace the regions for this game and layout (none: watch the whole picture)."""
        with self._lock:
            entry = self._games.setdefault(game, {}).setdefault(layout(size), {})
            entry["regions"] = [{"name": r.name, "box": [round(v, 4) for v in r.box]} for r in regions]
        self._save()

    def suggestion(self, box: Pixels, lines: int, size: tuple[int, int]) -> Box:
        """A region around one block of text, with room for its name tag and longer lines."""
        return _padded([Observation(_fractions(box, size), (box[3] - box[1]) / max(lines, 1) / size[1])])

    def _save(self) -> None:
        with self._lock:
            text = json.dumps({"version": _VERSION, "games": self._games}, ensure_ascii=False, indent=1)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(text, encoding="utf-8")


def _fractions(box: Pixels, size: tuple[int, int]) -> Box:
    width, height = size
    left, top, right, bottom = box
    return (left / width, top / height, right / width, bottom / height)


def _padded(group: list[Observation]) -> Box:
    line = float(np.median([o.line_height for o in group]))
    left = min(o.box[0] for o in group) - _PAD_SIDES * line
    top = min(o.box[1] for o in group) - _PAD_TOP * line
    right = max(o.box[2] for o in group) + _PAD_SIDES * line
    bottom = max(o.box[3] for o in group) + _PAD_BOTTOM * line
    return (max(left, 0.0), max(top, 0.0), min(right, 1.0), min(bottom, 1.0))


def to_pixels(region: Box, picture: Pixels) -> Pixels:
    """`region` of the picture at `picture` (left, top, right, bottom in window pixels), in
    window pixels."""
    p_left, p_top, p_right, p_bottom = picture
    width, height = p_right - p_left, p_bottom - p_top
    left, top, right, bottom = region
    return (
        p_left + round(left * width),
        p_top + round(top * height),
        p_left + round(right * width),
        p_top + round(bottom * height),
    )


def in_picture(box: Pixels, picture: Pixels) -> tuple[Pixels, tuple[int, int]]:
    """A box in window pixels as (box in picture pixels, picture size), for DialogueBoxes."""
    p_left, p_top, p_right, p_bottom = picture
    left, top, right, bottom = box
    return (left - p_left, top - p_top, right - p_left, bottom - p_top), (p_right - p_left, p_bottom - p_top)


# --- Finding the game picture ------------------------------------------------------------

# Brightness at or below which a pixel is part of a black bar, and the share of a row (or,
# within the picture's rows, a column) that must be brighter for it to be picture. Rows are
# lenient so a dark cave still counts; columns strict so a small second screen beside the
# picture (a 3DS emulator's touch screen) doesn't.
_BLACK = 4
_LIT_ROW, _LIT_COLUMN = 0.05, 0.35


def picture_box(image: Image.Image) -> Pixels:
    """Where the game picture is in a window capture: the largest area between black bars,
    leaving out the emulator's menu and status bars. The whole image if there are no bars."""
    lit = np.asarray(image.convert("L")) > _BLACK
    height, width = lit.shape
    top, bottom = _longest_run(lit.mean(axis=1) > _LIT_ROW)
    if bottom - top < 0.25 * height:
        return 0, 0, width, height
    left, right = _longest_run(lit[top:bottom].mean(axis=0) > _LIT_COLUMN)
    if right - left < 0.25 * width:
        return 0, 0, width, height
    return left, top, right, bottom


def _longest_run(flags: np.ndarray) -> tuple[int, int]:
    """(start, end) of the longest run of True values."""
    best, start = (0, 0), None
    for i, flag in enumerate([*flags.tolist(), False]):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            if i - start > best[1] - best[0]:
                best = (start, i)
            start = None
    return best


class PictureFinder:
    """Keeps track of where the game picture is while watching, without capturing the whole
    window every frame: found again when the window's size changes, and checked every few
    seconds in case the emulator's layout changed (taken on once two checks agree)."""

    def __init__(self, every: float = 2.0) -> None:
        self.every = every
        self._size: tuple[int, int] | None = None
        self._box: Pixels | None = None
        self._pending: Pixels | None = None
        self._checked = 0.0

    def find(self, size: tuple[int, int], grab_window: Callable[[], Image.Image]) -> Pixels:
        now = time.monotonic()
        if self._box is None or size != self._size:
            self._size, self._box, self._pending, self._checked = size, picture_box(grab_window()), None, now
        elif now - self._checked >= self.every:
            self._checked = now
            found = picture_box(grab_window())
            if found == self._box:
                self._pending = None
            elif found == self._pending:
                self._box, self._pending = found, None
            else:
                self._pending = found
        return self._box


# --- Noticing new text -------------------------------------------------------------------

# The picture is shrunk to this width to look for text in it.
MAP_WIDTH = 320
# How much brighter (or darker) than its surroundings a pixel must be to count as a stroke.
_TEXT_CONTRAST = 100
# Share of a piece of text's strokes that must differ for it to count as changed, and below
# which consecutive frames count as the same (it has finished typing). Scenery moving behind
# a see-through box changes about 3% (up to 6%) of a still line's strokes.
_CHANGED = 0.12
_STILL = 0.07
# Frames in a row a piece of text must stay put before it's read.
_STILL_FRAMES = 2
# How much two boxes must overlap (intersection over union) to be the same piece of text.
_SAME_PLACE = 0.6
# ...and how much a piece's box may shift between frames for it to count as still.
_STILL_PLACE = 0.85
# Frames after which text no longer on screen is forgotten: shown again later, it's new.
_FORGET_FRAMES = 40
_KNOWN_MAX = 200
# Text this many of its line heights away from text still typing out is in the same box.
_BESIDE = 1.5

Detector = Callable[[np.ndarray], list[Pixels]]


def shrink(image: Image.Image) -> np.ndarray:
    """The picture `MAP_WIDTH` wide, as RGB pixels: what text is looked for in."""
    rgb = np.asarray(image.convert("RGB"))
    height = max(1, round(rgb.shape[0] * MAP_WIDTH / max(rgb.shape[1], 1)))
    return np.ascontiguousarray(cv2.resize(rgb, (MAP_WIDTH, height), interpolation=cv2.INTER_AREA))


def strokes(small: np.ndarray) -> np.ndarray:
    """The pixels that stand out sharply from their surroundings: text strokes, not scenery.
    Game text is drawn to be read over anything; scenery behind a see-through box is dimmed,
    and its outlines (an owl's wings against the sky) are softer. Brightness is each pixel's
    brightest channel, so blue or red text counts too."""
    value = small.astype(np.float32).max(axis=2)
    return np.abs(value - cv2.blur(value, (9, 9))) > _TEXT_CONTRAST


def difference(a: np.ndarray, b: np.ndarray) -> float:
    """Share of the stroke pixels in either map that differ between them."""
    if a.shape != b.shape:
        return 1.0
    either = np.count_nonzero(a | b)
    if either == 0:
        return 0.0
    return np.count_nonzero(a ^ b) / either


def overlap(a: Pixels, b: Pixels) -> float:
    """Intersection over union."""
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return 0.0
    area = lambda r: (r[2] - r[0]) * (r[3] - r[1])
    shared = width * height
    return shared / (area(a) + area(b) - shared)


@dataclass
class _Text:
    box: Pixels  # in the shrunk picture
    strokes: np.ndarray
    still: int = 0  # frames it has stayed put
    seen: int = 0  # frame it was last on screen (known text)


class TextWatcher:
    """Feed it frames of the game picture; it says when new text has settled and the picture
    should be read.

    Text is found with a text detector on a small copy of the picture, and each piece is
    followed on its own: one that is new, or whose strokes changed, is read once it has stayed
    put for a couple of frames. Text that never changes (a HUD, the date) is never new; a sign
    drifting past as the camera turns never stays put; scenery moving elsewhere on screen
    doesn't hold up the dialogue box. A choice appearing under a line, or a thought bubble
    popping up, is new text like any other.
    """

    def __init__(self, detect: Detector) -> None:
        self._detect = detect
        self._frame = 0
        self._current: list[_Text] = []
        self._known: list[_Text] = []  # text that has settled: read, or there all along

    def update(self, image: Image.Image, regions: list[Box] | None = None) -> bool:
        """`regions`: only text inside these parts of the picture counts (all of it if none)."""
        self._frame += 1
        small = shrink(image)
        stroke_map = strokes(small)
        height, width = stroke_map.shape
        boxes = [b for b in self._detect(small) if _inside(b, (width, height), regions)]
        current = []
        for box in boxes:
            piece = _Text(box, _crop(stroke_map, box))
            before = max(self._current, key=lambda t: overlap(t.box, box), default=None)
            if before is not None and overlap(before.box, box) >= _STILL_PLACE:
                # Compared where it was, so a box found a pixel wider isn't a change. (A line
                # typing out grows: its box moves on more than that.)
                if difference(_crop(stroke_map, before.box), before.strokes) <= _STILL:
                    piece.still = before.still + 1
            current.append(piece)
        self._current = current

        # Text that has settled and wasn't there before (or looked different). Any version of
        # it seen lately counts: Navi flying over a line changes it, and it comes back as it was.
        new = []
        for piece in current:
            if piece.still < _STILL_FRAMES:
                continue
            versions = [t for t in self._known if overlap(t.box, piece.box) >= _SAME_PLACE]
            matches = [t for t in versions if difference(_crop(stroke_map, t.box), t.strokes) <= _CHANGED]
            for t in matches:
                t.seen = self._frame
            if not matches:
                new.append(piece)
        # Wait while text beside it is still changing: the rest of the box typing out.
        typing = [t for t in current if t.still < _STILL_FRAMES]
        if not new or any(_beside(t.box, n.box) for t in typing for n in new):
            self._known = [t for t in self._known if self._frame - t.seen <= _FORGET_FRAMES]
            return False
        self._known += [_Text(n.box, n.strokes, seen=self._frame) for n in new]
        self._known = [t for t in self._known if self._frame - t.seen <= _FORGET_FRAMES][-_KNOWN_MAX:]
        return True


def _beside(a: Pixels, b: Pixels) -> bool:
    """Close enough to be in one text box: within a line height or so of each other."""
    reach = max(a[3] - a[1], b[3] - b[1]) * _BESIDE
    return a[0] - reach < b[2] and b[0] - reach < a[2] and a[1] - reach < b[3] and b[1] - reach < a[3]


def _crop(stroke_map: np.ndarray, box: Pixels) -> np.ndarray:
    left, top, right, bottom = box
    return stroke_map[max(top, 0) : max(bottom, 0), max(left, 0) : max(right, 0)]


def _inside(box: Pixels, size: tuple[int, int], regions: list[Box] | None) -> bool:
    if not regions:
        return True
    x = (box[0] + box[2]) / 2 / size[0]
    y = (box[1] + box[3]) / 2 / size[1]
    return any(r[0] <= x <= r[2] and r[1] <= y <= r[3] for r in regions)
