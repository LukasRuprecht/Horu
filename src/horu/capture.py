"""Screen capture: the focused window, a whole monitor, or a fixed region."""

from __future__ import annotations

import sys

import mss
from PIL import Image

from horu.config import CaptureSettings, Region

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32")
    # Work in physical pixels so window coordinates match what mss captures.
    try:
        _user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except (AttributeError, OSError):
        pass


def list_monitors() -> list[dict]:
    """Monitors as mss reports them. Index 0 is the virtual all-monitors screen."""
    with mss.mss() as sct:
        return list(sct.monitors)


def grab(settings: CaptureSettings) -> Image.Image:
    if settings.mode == "window":
        box = focused_window_box()
        if box is not None:
            return _grab_box(box)
        # No usable foreground window (or not on Windows): fall back to the monitor.
    if settings.mode == "region":
        if settings.region is None:
            raise ValueError('capture mode "region" needs a region in config.toml')
        return grab_region(settings.region, settings.monitor)
    return grab_region(None, settings.monitor)


def grab_region(region: Region | None, monitor: int = 1) -> Image.Image:
    """Capture `region` relative to `monitor`'s top-left corner, or the whole monitor."""
    with mss.mss() as sct:
        monitors = sct.monitors
        if not 1 <= monitor < len(monitors):
            raise ValueError(f"Monitor {monitor} not found (have 1..{len(monitors) - 1})")
        mon = monitors[monitor]
    if region is None:
        return _grab_box(mon)
    return _grab_box(
        {"left": mon["left"] + region.left, "top": mon["top"] + region.top, "width": region.width, "height": region.height}
    )


def focused_window_box() -> dict | None:
    """Screen box of the foreground window's client area (the game, without its title bar)."""
    if sys.platform != "win32":
        return None
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return None
    rect = wintypes.RECT()
    if not _user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    origin = wintypes.POINT(0, 0)
    _user32.ClientToScreen(hwnd, ctypes.byref(origin))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width < 50 or height < 50:
        return None
    return {"left": origin.x, "top": origin.y, "width": width, "height": height}


def _grab_box(box: dict) -> Image.Image:
    # A fresh mss instance per call: mss handles are not safe to share across threads.
    with mss.mss() as sct:
        shot = sct.grab({k: box[k] for k in ("left", "top", "width", "height")})
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
