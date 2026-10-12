"""Text from a text hooker: exact game text, read from the game's memory, with no OCR.

Horu doesn't hook games itself. A hooker (Textractor, LunaHook, Agent, ...) attaches to the
game and sends out each line it finds. Horu listens in one of two ways:

- the clipboard: the hooker copies each line (Textractor's "Copy to Clipboard" extension,
  LunaTranslator's clipboard output). Works with almost any hooker.
- a WebSocket: the hooker serves each line to connected clients (Textractor with the
  textractor_websocket extension, LunaTranslator, Agent).
"""

from __future__ import annotations

import re
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable

DEFAULT_URL = "ws://127.0.0.1:6677"  # textractor_websocket's address
# Longer than any line of game text: someone copying a web page, not a hooker.
_MAX_CHARS = 400
_CLIPBOARD_SECONDS = 0.2
_RECONNECT_SECONDS = 3.0
_JAPANESE = re.compile(r"[぀-ヿ㐀-䶿一-鿿ｦ-ﾟ]")
# A name before the line: 【名前】…, 名前「…」, or a name on its own line above a quote.
_BRACKETED_NAME = re.compile(r"^[【\[]([^】\]\n]{1,16})[】\]]\s*(.+)$", re.S)
# The quote runs to the end, so ちょっと「待って」と言った keeps its first word.
_NAME_THEN_QUOTE = re.compile(r"^([^「『（(\s。、！？!?…‥]{1,12})\s*([「『（(].*[」』）)])$", re.S)


@dataclass(frozen=True)
class Hooked:
    text: str
    speaker: str | None = None


def parse(raw: str) -> Hooked | None:
    """A hooked line as Horu shows it: the speaker split off when the hooker includes one,
    and the line's wrapping removed. None when it isn't Japanese game text."""
    text = raw.replace("\r", "").strip().strip("　")
    if not text or len(text) > _MAX_CHARS or not _JAPANESE.search(text):
        return None
    speaker = None
    match = _BRACKETED_NAME.match(text) or _NAME_THEN_QUOTE.match(text)
    if match:
        speaker, text = match.group(1).strip(), match.group(2)
    # Games wrap lines to fit their text box; Japanese doesn't need a space where they join.
    text = "".join(line.strip().lstrip("　") for line in text.split("\n"))
    if not _JAPANESE.search(text):
        return None
    return Hooked(text, speaker or None)


class Listener:
    """Calls `on_text` with each line a hooker sends, from a background thread, and
    `on_state` with (state, message) as the connection changes."""

    def __init__(self, on_text: Callable[[str], None], on_state: Callable[[str, str], None]) -> None:
        self.on_text = on_text
        self.on_state = on_state
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=f"horu-{type(self).__name__}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        raise NotImplementedError


class ClipboardListener(Listener):
    """Lines the hooker copies to the clipboard. `ignore()` says whether to skip a change
    (one made while Horu itself is in front: the user copying from Horu)."""

    def __init__(self, on_text, on_state, ignore: Callable[[], bool] = lambda: False) -> None:
        super().__init__(on_text, on_state)
        self.ignore = ignore

    def _run(self) -> None:
        if sys.platform != "win32":
            self.on_state("error", "Reading hooked text from the clipboard works on Windows only.")
            return
        self.on_state("listening", "Listening to the clipboard for hooked text.")
        seen = _clipboard_sequence()  # what's on the clipboard already isn't new
        while not self._stop.wait(_CLIPBOARD_SECONDS):
            number = _clipboard_sequence()
            if number == seen:
                continue
            seen = number
            if self.ignore():
                continue
            text = _clipboard_text()
            if text:
                self.on_text(text)


class WebSocketListener(Listener):
    """Lines the hooker serves on a WebSocket; reconnects while the hooker isn't running."""

    def __init__(self, on_text, on_state, url: str = DEFAULT_URL) -> None:
        super().__init__(on_text, on_state)
        self.url = url

    def _run(self) -> None:
        from websockets.exceptions import ConnectionClosed
        from websockets.sync.client import connect

        while not self._stop.is_set():
            try:
                with connect(self.url, open_timeout=_RECONNECT_SECONDS, max_size=2**20) as socket:
                    self.on_state("listening", f"Connected to the text hooker at {self.url}.")
                    while not self._stop.is_set():
                        try:
                            message = socket.recv(timeout=0.5)
                        except TimeoutError:
                            continue
                        if isinstance(message, bytes):
                            message = message.decode("utf-8", errors="replace")
                        self.on_text(message)
            except ConnectionClosed:
                self.on_state("waiting", f"The text hooker at {self.url} disconnected. Waiting for it...")
            except (OSError, TimeoutError) as e:
                self.on_state("waiting", f"No text hooker at {self.url} yet ({_reason(e)}). Waiting for it...")
            except Exception as e:  # a bad address, or something that isn't a WebSocket server
                self.on_state("error", f"Can't connect to {self.url}: {e}")
            self._stop.wait(_RECONNECT_SECONDS)


def _reason(error: Exception) -> str:
    if isinstance(error, ConnectionRefusedError):
        return "nothing is listening there"
    if isinstance(error, TimeoutError):
        return "no answer"
    return str(error) or type(error).__name__


# --- the Windows clipboard ------------------------------------------------------------

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
    _user32.OpenClipboard.argtypes = [wintypes.HWND]
    _user32.GetClipboardData.argtypes = [wintypes.UINT]
    _user32.GetClipboardData.restype = wintypes.HANDLE
    _user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    _kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    _kernel32.GlobalLock.restype = ctypes.c_void_p
    _kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    _CF_UNICODETEXT = 13


def _clipboard_sequence() -> int:
    return _user32.GetClipboardSequenceNumber()


def _clipboard_text() -> str | None:
    """The clipboard's text, or None (not text, or another program has it open)."""
    if not _user32.IsClipboardFormatAvailable(_CF_UNICODETEXT):
        return None
    for _ in range(5):  # the program that just copied may still have it open
        if _user32.OpenClipboard(None):
            break
        time.sleep(0.02)
    else:
        return None
    try:
        handle = _user32.GetClipboardData(_CF_UNICODETEXT)
        if not handle:
            return None
        pointer = _kernel32.GlobalLock(handle)
        if not pointer:
            return None
        try:
            return ctypes.wstring_at(pointer)
        finally:
            _kernel32.GlobalUnlock(handle)
    finally:
        _user32.CloseClipboard()
