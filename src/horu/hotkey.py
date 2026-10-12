"""Global hotkeys.

On Windows this uses the native RegisterHotKey API: it works while a game has focus and
fails loudly if another program already owns the combination. Elsewhere it falls back to
pynput.
"""

from __future__ import annotations

import sys
import threading
from typing import Callable


class HotkeyError(Exception):
    pass


def listen(spec: str, callback: Callable[[], None]) -> Callable[[], None]:
    """Call `callback` whenever `spec` (e.g. "<ctrl>+<alt>+j") is pressed. Returns a stop function."""
    if sys.platform == "win32":
        return _listen_win32(spec, callback)
    return _listen_pynput(spec, callback)


# --- Windows -------------------------------------------------------------------------

_MODIFIERS = {"alt": 0x1, "ctrl": 0x2, "shift": 0x4, "win": 0x8, "cmd": 0x8}
_MOD_NOREPEAT = 0x4000
_NAMED_KEYS = {
    "space": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "backspace": 0x08,
    "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23,
    "page_up": 0x21, "page_down": 0x22, "pause": 0x13, "print_screen": 0x2C,
}
_WM_HOTKEY = 0x0312
_WM_QUIT = 0x0012
_ERROR_HOTKEY_ALREADY_REGISTERED = 1409


def parse_win32(spec: str) -> tuple[int, int]:
    """Parse a pynput-style hotkey string into (modifier flags, virtual-key code)."""
    mods, vk = 0, None
    for part in spec.lower().split("+"):
        name = part.strip().strip("<>")
        if name in _MODIFIERS:
            mods |= _MODIFIERS[name]
        elif vk is not None:
            raise HotkeyError(f"Hotkey {spec!r} has more than one non-modifier key")
        elif len(name) == 1 and name.isalnum():
            vk = ord(name.upper())
        elif name.startswith("f") and name[1:].isdigit() and 1 <= int(name[1:]) <= 24:
            vk = 0x70 + int(name[1:]) - 1
        elif name in _NAMED_KEYS:
            vk = _NAMED_KEYS[name]
        else:
            raise HotkeyError(f"Unknown key {name!r} in hotkey {spec!r}")
    if vk is None:
        raise HotkeyError(f"Hotkey {spec!r} needs a non-modifier key")
    return mods, vk


def _listen_win32(spec: str, callback: Callable[[], None]) -> Callable[[], None]:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32")
    mods, vk = parse_win32(spec)
    ready = threading.Event()
    state: dict = {}

    # RegisterHotKey posts WM_HOTKEY to the registering thread, so the message loop must
    # run on the same thread that registers.
    def run() -> None:
        state["thread_id"] = kernel32.GetCurrentThreadId()
        if not user32.RegisterHotKey(None, 1, mods | _MOD_NOREPEAT, vk):
            state["error"] = ctypes.get_last_error()
            ready.set()
            return
        ready.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == _WM_HOTKEY:
                    callback()
        finally:
            user32.UnregisterHotKey(None, 1)

    thread = threading.Thread(target=run, name="horu-hotkey", daemon=True)
    thread.start()
    ready.wait()
    if "error" in state:
        if state["error"] == _ERROR_HOTKEY_ALREADY_REGISTERED:
            raise HotkeyError(f"{spec} is already used by another program.")
        raise HotkeyError(f"Could not register hotkey {spec} (Windows error {state['error']})")

    def stop() -> None:
        user32.PostThreadMessageW(state["thread_id"], _WM_QUIT, 0, 0)
        thread.join(timeout=1)

    return stop


# --- Other platforms -----------------------------------------------------------------


def _listen_pynput(spec: str, callback: Callable[[], None]) -> Callable[[], None]:
    from pynput import keyboard

    listener = keyboard.GlobalHotKeys({spec: callback})
    listener.start()
    return listener.stop
