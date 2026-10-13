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
    _dwmapi = ctypes.WinDLL("dwmapi")
    _DWMWA_EXTENDED_FRAME_BOUNDS = 9
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


def covered(hwnd: int, box: dict, part: tuple[int, int, int, int], own_ok: bool = False, grid: tuple[int, int] = (8, 6)) -> tuple[float, list[tuple[int, int, int, int]]]:
    """How much of `part` (in pixels of the window's box) other windows cover on screen: the
    share of a grid of points across it that show another window, and those windows'
    rectangles (in the same pixels, clipped to `part`). Horu's own window doesn't count with
    `own_ok` (when it hides from captures, the game shows through it in captures). Nor do the
    invisible resize borders Windows 10 and 11 put around windows: a window beside the game
    reaches a few pixels over it without covering anything."""
    if sys.platform != "win32":
        return 0.0, []
    left, top, right, bottom = part
    columns, rows = grid
    points = [
        (left + (right - left) * (2 * c + 1) // (2 * columns), top + (bottom - top) * (2 * r + 1) // (2 * rows))
        for r in range(rows)
        for c in range(columns)
    ]
    hidden, rects = 0, {}
    for x, y in points:
        screen_x, screen_y = box["left"] + x, box["top"] + y
        shown = _user32.WindowFromPoint(wintypes.POINT(screen_x, screen_y))
        root = _user32.GetAncestor(shown, _GA_ROOT) if shown else None
        if not root or root & 0xFFFFFFFF == hwnd & 0xFFFFFFFF:
            continue
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(root, ctypes.byref(pid))
        if own_ok and pid.value == os.getpid():
            continue
        frame = _visible_frame(root)
        if frame and not (frame[0] <= screen_x < frame[2] and frame[1] <= screen_y < frame[3]):
            continue  # its invisible border
        hidden += 1
        if frame and root not in rects and frame[2] - box["left"] > left and frame[3] - box["top"] > top:
            rects[root] = (
                max(frame[0] - box["left"], left), max(frame[1] - box["top"], top),
                min(frame[2] - box["left"], right), min(frame[3] - box["top"], bottom),
            )
    return hidden / len(points), list(rects.values())


def _visible_frame(hwnd: int) -> tuple[int, int, int, int] | None:
    """A window's rectangle on screen as drawn, without its invisible resize borders."""
    rect = wintypes.RECT()
    if _dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), _DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect)):
        if not _user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
            return None
    return rect.left, rect.top, rect.right, rect.bottom


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


def window_title(hwnd: int) -> str:
    if sys.platform != "win32" or not hwnd:
        return ""
    buffer = ctypes.create_unicode_buffer(256)
    _user32.GetWindowTextW(hwnd, buffer, 256)
    return buffer.value


def game_windows() -> list[dict]:
    """Windows a game could be in: visible, titled, on screen, big enough, and not Horu's own.
    [{"id", "title", "process"}], front-most first."""
    if sys.platform != "win32":
        return []
    found: list[dict] = []
    cloaked = wintypes.DWORD()
    DWMWA_CLOAKED, GWL_EXSTYLE, WS_EX_TOOLWINDOW = 14, -20, 0x80

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        if not _user32.IsWindowVisible(hwnd) or _user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return True
        # Hidden store-app windows report as visible but are "cloaked".
        if not _dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked)) and cloaked.value:
            return True
        title, box = window_title(hwnd), window_box(hwnd)
        if not title or box is None or box["width"] < 200 or box["height"] < 150:
            return True
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != os.getpid():
            found.append({"id": int(hwnd), "title": title, "process": process_name(hwnd) or ""})
        return True

    _user32.EnumWindows(visit, 0)
    return found


def grab_window(hwnd: int, own_ok: bool = False) -> tuple[Image.Image, float] | None:
    """A capture of a window's client area wherever it is on screen, with any part another
    window covers blanked out, and the share covered. None if it's gone or minimized."""
    box = window_box(hwnd)
    if box is None:
        return None
    share, rects = covered(hwnd, box, (0, 0, box["width"], box["height"]), own_ok=own_ok)
    image = _grab_box(box)
    for rect in rects:
        image.paste((0, 0, 0), rect)
    return image, share


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
