"""Screen capture of a configured region."""

from __future__ import annotations

import mss
from PIL import Image

from horu.config import Region


def list_monitors() -> list[dict]:
    """Monitors as mss reports them. Index 0 is the virtual all-monitors screen."""
    with mss.mss() as sct:
        return list(sct.monitors)


def grab(region: Region, monitor: int = 1) -> Image.Image:
    """Capture `region`, given relative to `monitor`'s top-left corner."""
    # A fresh mss instance per call: mss handles are not safe to share across threads.
    with mss.mss() as sct:
        monitors = sct.monitors
        if not 1 <= monitor < len(monitors):
            raise ValueError(f"Monitor {monitor} not found (have 1..{len(monitors) - 1})")
        mon = monitors[monitor]
        box = {
            "left": mon["left"] + region.left,
            "top": mon["top"] + region.top,
            "width": region.width,
            "height": region.height,
        }
        shot = sct.grab(box)
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
