"""Auto-capture: read the dialogue box by itself whenever its text changes.

Each game draws dialogue in one place. Horu learns where from the captures the player makes
with the hotkey (the dialogue blocks it found), per game, and once a few agree, watches only
that region: menus, battle UI, and popups elsewhere on screen are never read. Positions are
kept relative to the game picture, not the window: emulators centre the picture between
black bars that change with the window's size.

Watching is cheap: a few times a second the region is shrunk and turned into a map of the
pixels that stand out sharply (text strokes). Comparing those rather than all pixels means
moving scenery behind a see-through box doesn't count as a change. A change is read once
the text stops changing (it has finished typing out).
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

# Captures that must agree on the box before it's used, and how many are remembered.
_AGREE = 3
_KEEP = 12
# Room around the learned text area, in line heights: the name tag sits above the text, and
# longer lines can reach further than the ones seen so far.
_PAD_TOP, _PAD_BOTTOM, _PAD_SIDES = 1.8, 0.6, 1.5

Box = tuple[float, float, float, float]  # left, top, right, bottom as fractions of the game picture
Pixels = tuple[int, int, int, int]


@dataclass(frozen=True)
class Observation:
    box: Box
    line_height: float  # as a fraction of the picture height

    def overlaps(self, other: Observation) -> bool:
        """Most of the smaller box lies inside the other: the same dialogue box, holding
        text of different lengths."""
        left, top = max(self.box[0], other.box[0]), max(self.box[1], other.box[1])
        right, bottom = min(self.box[2], other.box[2]), min(self.box[3], other.box[3])
        if right <= left or bottom <= top:
            return False
        area = lambda b: (b[2] - b[0]) * (b[3] - b[1])
        return (right - left) * (bottom - top) >= 0.5 * min(area(self.box), area(other.box))


class DialogueBoxes:
    """Where each game shows its dialogue, learned from the player's captures."""

    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        self.path = path
        self._lock = threading.Lock()
        try:
            self._data: dict[str, dict] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._data = {}
        # Boxes learned before they were kept relative to the picture are in other units.
        self._data = {game: data for game, data in self._data.items() if data.get("frame") == "picture"}

    def observe(self, game: str, box: tuple[int, int, int, int], lines: int, size: tuple[int, int]) -> None:
        """Remember a dialogue block found in a game picture of `size` (width, height)."""
        width, height = size
        left, top, right, bottom = box
        entry = {"box": [left / width, top / height, right / width, bottom / height], "line": (bottom - top) / max(lines, 1) / height}
        with self._lock:
            game_data = self._data.setdefault(game, {"frame": "picture"})
            seen = game_data.setdefault("seen", [])
            seen.append(entry)
            del seen[:-_KEEP]
        self._save()

    def set(self, game: str, box: tuple[int, int, int, int], lines: int, size: tuple[int, int]) -> None:
        """Use this block's position as the game's dialogue box, replacing what was learned."""
        width, height = size
        left, top, right, bottom = box
        line = (bottom - top) / max(lines, 1) / height
        with self._lock:
            self._data[game] = {"frame": "picture", "seen": [], "fixed": {"box": [left / width, top / height, right / width, bottom / height], "line": line}}
        self._save()

    def region(self, game: str) -> Box | None:
        """The part of the game picture to watch, or None until enough captures agree."""
        data = self._data.get(game, {})
        if data.get("fixed"):
            group = [_observation(data["fixed"])]
        else:
            group = _largest_group([_observation(e) for e in data.get("seen", [])])
            if len(group) < _AGREE:
                return None
        line = float(np.median([o.line_height for o in group]))
        left = min(o.box[0] for o in group) - _PAD_SIDES * line
        top = min(o.box[1] for o in group) - _PAD_TOP * line
        right = max(o.box[2] for o in group) + _PAD_SIDES * line
        bottom = max(o.box[3] for o in group) + _PAD_BOTTOM * line
        return (max(left, 0.0), max(top, 0.0), min(right, 1.0), min(bottom, 1.0))

    def progress(self, game: str) -> tuple[int, int]:
        """(captures agreeing so far, captures needed)."""
        data = self._data.get(game, {})
        if data.get("fixed"):
            return _AGREE, _AGREE
        return min(len(_largest_group([_observation(e) for e in data.get("seen", [])])), _AGREE), _AGREE

    def _save(self) -> None:
        with self._lock:
            text = json.dumps(self._data, ensure_ascii=False, indent=1)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(text, encoding="utf-8")


def _observation(entry: dict) -> Observation:
    return Observation(tuple(entry["box"]), entry["line"])


def _largest_group(observations: list[Observation]) -> list[Observation]:
    """The observations around the one that overlaps the most others (ties: the latest)."""
    best: list[Observation] = []
    for candidate in reversed(observations):
        group = [o for o in observations if o is candidate or o.overlaps(candidate)]
        if len(group) > len(best):
            best = group
    return best


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

# Text maps are compared at this width.
_MAP_WIDTH = 320
# How much brighter (or darker) than its surroundings a pixel must be to count as text.
_TEXT_CONTRAST = 100
# Share of text pixels that must differ between frames for text to count as changed, and
# below which consecutive frames count as the same (the text has finished typing). Scenery
# moving behind a see-through box changes about 3% (up to 6%) of a still line's map.
_CHANGED = 0.12
_STILL = 0.07
# Frames in a row that must be still before a change is read.
_STILL_FRAMES = 2


def edge_map(image: Image.Image) -> np.ndarray:
    """A small map of the pixels in `image` that stand out sharply from their surroundings:
    text strokes, not scenery. Game text is drawn to be read over anything; scenery behind
    a see-through box is dimmed, and its outlines (an owl's wings against the sky) are softer.
    Brightness is each pixel's brightest channel, so blue or red text counts too."""
    rgb = np.asarray(image.convert("RGB"))
    height = max(1, round(rgb.shape[0] * _MAP_WIDTH / max(rgb.shape[1], 1)))
    small = cv2.resize(rgb, (_MAP_WIDTH, height), interpolation=cv2.INTER_AREA).astype(np.float32)
    value = small.max(axis=2)
    return np.abs(value - cv2.blur(value, (9, 9))) > _TEXT_CONTRAST


def difference(a: np.ndarray, b: np.ndarray) -> float:
    """Share of the edge pixels in either map that differ between them."""
    if a.shape != b.shape:
        return 1.0
    either = np.count_nonzero(a | b)
    if either == 0:
        return 0.0
    return np.count_nonzero(a ^ b) / either


class ChangeWatcher:
    """Feed it frames of the dialogue box; it says when new text has settled and should be
    read."""

    def __init__(self) -> None:
        self._previous: np.ndarray | None = None
        self._read: np.ndarray | None = None  # the frame last read
        self._still = 0

    def update(self, image: Image.Image) -> bool:
        current = edge_map(image)
        moving = self._previous is None or difference(current, self._previous) > _STILL
        self._previous = current
        self._still = 0 if moving else self._still + 1
        if self._still < _STILL_FRAMES:
            return False
        if self._read is not None and difference(current, self._read) <= _CHANGED:
            return False
        self._read = current
        return True

    def reset(self) -> None:
        self._previous = self._read = None
        self._still = 0
