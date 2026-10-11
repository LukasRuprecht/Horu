"""Screen capture: the focused window, a whole monitor, or a fixed region."""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

import mss
from PIL import Image

from horu.config import CaptureSettings, Region

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32")
    _kernel32 = ctypes.WinDLL("kernel32")
    _user32.WindowFromPoint.argtypes = [wintypes.POINT]
    _user32.WindowFromPoint.restype = wintypes.HWND
    _user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    _user32.GetAncestor.restype = wintypes.HWND
    _GA_ROOT = 2
    # Work in physical pixels so window coordinates match what mss captures.
    try:
        _user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except (AttributeError, OSError):
        pass


def list_monitors() -> list[dict]:
    """Monitors as mss reports them. Index 0 is the virtual all-monitors screen."""
    with mss.MSS() as sct:
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
    with mss.MSS() as sct:
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
    return window_box(_user32.GetForegroundWindow())


def foreground_window() -> int | None:
    """Handle of the foreground window, to keep watching it after focus moves elsewhere."""
    if sys.platform != "win32":
        return None
    return _user32.GetForegroundWindow() or None


def window_box(hwnd: int) -> dict | None:
    """Screen box of a window's client area; None if it's gone, minimized, or tiny."""
    if sys.platform != "win32" or not hwnd or not _user32.IsWindow(hwnd) or _user32.IsIconic(hwnd):
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


def grab_part(box: dict, part: tuple[int, int, int, int]) -> Image.Image:
    """Capture `part` (left, top, right, bottom, in pixels of the box) of a screen box."""
    left, top, right, bottom = part
    return _grab_box({"left": box["left"] + left, "top": box["top"] + top, "width": right - left, "height": bottom - top})


def shows_part(hwnd: int, box: dict, part: tuple[int, int, int, int], own_ok: bool = False) -> bool:
    """True when nothing covers `part` of the window's box on screen: its corners and centre
    show the window itself (or Horu's own window, with `own_ok`, when Horu hides from captures)."""
    if sys.platform != "win32":
        return True
    left, top, right, bottom = part
    points = [(left, top), (right - 1, top), (left, bottom - 1), (right - 1, bottom - 1), ((left + right) // 2, (top + bottom) // 2)]
    for x, y in points:
        shown = _user32.WindowFromPoint(wintypes.POINT(box["left"] + x, box["top"] + y))
        root = _user32.GetAncestor(shown, _GA_ROOT) if shown else None
        if root and root & 0xFFFFFFFF == hwnd & 0xFFFFFFFF:  # handles fit in 32 bits
            continue
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(root, ctypes.byref(pid))
        if not (own_ok and pid.value == os.getpid()):
            return False
    return True


def focused_window_is_own() -> bool:
    """True when Horu's own window has focus (so "capture the focused window" would capture Horu)."""
    if sys.platform != "win32":
        return False
    hwnd = _user32.GetForegroundWindow()
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value == os.getpid()


def exclude_from_capture(hwnd: int, exclude: bool = True) -> bool:
    """Hide a window from screen captures, so Horu never reads its own window even when it
    sits on top of the game. Also hides it from OBS, screen sharing, and screenshots.
    Needs Windows 10 2004 or later; returns False where unsupported."""
    if sys.platform != "win32":
        return False
    WDA_NONE, WDA_EXCLUDEFROMCAPTURE = 0x0, 0x11
    return bool(_user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE if exclude else WDA_NONE))


def focused_process_name() -> str | None:
    """The executable name of the focused window's program (e.g. "P5R.exe"), used to keep
    per-game settings apart."""
    if sys.platform != "win32":
        return None
    return process_name(_user32.GetForegroundWindow())


def process_name(hwnd: int) -> str | None:
    """The executable name of a window's program."""
    if sys.platform != "win32" or not hwnd:
        return None
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return None
    try:
        buffer = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buffer))
        if not _kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        return Path(buffer.value).name
    finally:
        _kernel32.CloseHandle(handle)


def save(image: Image.Image, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{datetime.now():%Y%m%d-%H%M%S-%f}.png"
    image.save(path)
    return path


def _grab_box(box: dict) -> Image.Image:
    # A fresh mss instance per call: mss handles are not safe to share across threads.
    with mss.MSS() as sct:
        shot = sct.grab({k: box[k] for k in ("left", "top", "width", "height")})
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
