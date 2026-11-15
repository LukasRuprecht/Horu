"""Recording what auto-capture saw, to measure when it reads and try out better rules.

While recording, every look the watcher takes at the game is logged, with the pictures
needed to replay it later (evals/replay.py) under a different rule and score the result:
did each new line get read, how late, and how many reads found nothing new.

A recording is a folder under debug/recordings:

    frames/000123.jpg   the game picture as the watcher sees it (shrunk), whenever it changed
    full/000123.jpg     the whole game window: about once a second while the picture is
                        changing, and at every look that set off a read. Read offline to
                        find what text was really on screen.
    log.jsonl           one line per look (time, which frame, regions, whether it set off a
                        read), and one per read with the lines it showed

Recordings hold screenshots of the game and nothing else from the PC. They're big (hundreds
of MB for a quarter of an hour), so a recording stops by itself after LIMIT_SECONDS.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import numpy as np
from PIL import Image

from horu import autocapture

DEFAULT_DIR = Path("debug/recordings")
LIMIT_SECONDS = 20 * 60
# A shrunk picture is kept when this many of its pixels differ clearly from the last one kept.
# Counted, not averaged: a short line changing (P5R's 別に。) moves a few dozen pixels a lot,
# and the average hardly at all.
_CLEARLY, _CHANGED_PIXELS = 30, 12
_FULL_EVERY = 1.0  # seconds between whole-window pictures while the game picture changes
_FULL_WIDTH = 1600  # ...saved at most this wide: enough to read the text offline


class Recorder:
    def __init__(self, game: str, root: Path = DEFAULT_DIR, limit: float = LIMIT_SECONDS) -> None:
        name = "".join(c if c.isalnum() else "_" for c in game)[:40] or "game"
        self.path = root / f"{time.strftime('%Y%m%d-%H%M%S')}-{name}"
        (self.path / "frames").mkdir(parents=True, exist_ok=True)
        (self.path / "full").mkdir(exist_ok=True)
        self._log = (self.path / "log.jsonl").open("w", encoding="utf-8")
        self._lock = threading.Lock()
        self._started = time.monotonic()
        self._limit = limit
        self._looks = 0
        self._frame: int | None = None  # the look whose shrunk picture was last kept
        self._small: np.ndarray | None = None
        self._full_at = -_FULL_EVERY
        self._full_frame: int | None = None  # the frame the last whole-window picture showed
        self.bytes = 0
        self._write({"event": "start", "game": game, "map_width": autocapture.MAP_WIDTH, "time": time.strftime("%Y-%m-%d %H:%M:%S")})

    @property
    def seconds(self) -> float:
        return time.monotonic() - self._started

    @property
    def expired(self) -> bool:
        return self.seconds >= self._limit

    def look(self, image: Image.Image, picture: autocapture.Pixels, regions: list[autocapture.Box], fired: bool, fresh: bool) -> None:
        """One look by the watcher: `image` is the game window, `picture` where the game
        picture is in it, `fired` whether this look set off a read, `fresh` whether the
        watcher had just started over (another game, window size, or set of regions)."""
        t = self.seconds
        small = autocapture.shrink(image.crop(picture))
        with self._lock:
            n = self._looks
            self._looks += 1
            changed = (
                self._small is None
                or self._small.shape != small.shape
                or int((np.abs(small.astype(np.int16) - self._small).max(axis=2) > _CLEARLY).sum()) >= _CHANGED_PIXELS
            )
            if changed:
                self._save(Image.fromarray(small), self.path / "frames" / f"{n:06d}.jpg", quality=92)
                self._frame, self._small = n, small
            full = None
            if fired or (self._full_frame != self._frame and t - self._full_at >= _FULL_EVERY):
                whole = image if image.width <= _FULL_WIDTH else image.resize((_FULL_WIDTH, round(image.height * _FULL_WIDTH / image.width)), Image.LANCZOS)
                self._save(whole, self.path / "full" / f"{n:06d}.jpg", quality=85)
                full, self._full_at, self._full_frame = n, t, self._frame
            self._write({
                "t": round(t, 3), "look": n, "frame": self._frame, "full": full, "size": list(image.size),
                "picture": list(picture), "regions": [list(r) for r in regions], "fired": fired, "fresh": fresh,
            })

    def note(self, event: str, **data) -> None:
        """Something else worth knowing when scoring: a read's lines, auto-capture pausing."""
        with self._lock:
            self._write({"t": round(self.seconds, 3), "event": event, **data})

    def close(self) -> None:
        with self._lock:
            if not self._log.closed:
                self._write({"t": round(self.seconds, 3), "event": "stop", "looks": self._looks})
                self._log.close()

    def _save(self, image: Image.Image, path: Path, quality: int) -> None:
        image.convert("RGB").save(path, format="JPEG", quality=quality)
        self.bytes += path.stat().st_size

    def _write(self, row: dict) -> None:
        if not self._log.closed:
            self._log.write(json.dumps(row, ensure_ascii=False) + "\n")
            self._log.flush()
