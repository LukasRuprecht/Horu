"""Horu's icon in the system tray (the notification area): click to show the window; the menu
captures, turns auto-capture on or off, or quits. With "minimize to tray" on, minimizing hides
the window from the taskbar, leaving just the tray icon."""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable

from horu import icon


class Tray:
    def __init__(
        self,
        show: Callable[[], None],
        capture: Callable[[], None],
        auto_on: Callable[[], bool],
        toggle_auto: Callable[[], None],
        quit: Callable[[], None],
    ) -> None:
        import pystray
        from PIL import Image

        item = pystray.MenuItem
        menu = pystray.Menu(
            item("Show Horu", lambda: show(), default=True),
            item("Capture now", lambda: capture()),
            item("Auto-capture", lambda: toggle_auto(), checked=lambda _: auto_on()),
            pystray.Menu.SEPARATOR,
            item("Quit Horu", lambda: quit()),
        )
        self._icon = pystray.Icon("Horu", Image.open(icon.ICO_PATH), "Horu", menu)

    def start(self) -> None:
        self._icon.run_detached()

    def stop(self) -> None:
        self._icon.stop()

    def notify(self, message: str) -> None:
        try:
            self._icon.notify(message, "Horu")
        except Exception:  # notifications are a nicety; some systems refuse them
            pass

    def refresh(self) -> None:
        """Redraw the menu's check marks (after auto-capture changed in the window)."""
        self._icon.update_menu()


def set_window_icon(hwnd: int) -> None:
    """Horu's icon on the window's title bar and taskbar button."""
    if sys.platform != "win32":
        return
    user32 = ctypes.windll.user32
    user32.LoadImageW.restype = ctypes.c_void_p
    user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
    IMAGE_ICON, LR_LOADFROMFILE, WM_SETICON = 1, 0x10, 0x80
    for kind, size in ((0, 16), (1, 32)):  # ICON_SMALL, ICON_BIG
        handle = user32.LoadImageW(None, str(icon.ICO_PATH), IMAGE_ICON, size, size, LR_LOADFROMFILE)
        if handle:
            user32.SendMessageW(hwnd, WM_SETICON, kind, handle)


def set_app_id() -> None:
    """Group Horu's taskbar button on its own (with its icon), not with other Python programs."""
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Horu.App")
        except (AttributeError, OSError):
            pass
