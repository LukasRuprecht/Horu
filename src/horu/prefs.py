"""Display preferences set in the Horu window, saved between sessions.

The window's browser storage is wiped on every launch (pywebview runs in private mode), so
the page reads and writes these through the Python API instead.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

DEFAULT_PATH = Path("data/ui_prefs.json")
DEFAULTS: dict = {
    "textSize": 26,  # px, dialogue text
    "furiganaScale": 0.5,  # furigana size relative to the text
    "furigana": True,
    "screenText": True,
    "onTop": False,
    # Keep Horu's window out of screen captures, so it's never read even when it overlaps the
    # game. Off by default: it also hides Horu from streams, remote desktops (Parsec), and
    # screenshots, which looks like Horu vanished. Parts of the game Horu covers are blanked.
    "hideFromCapture": False,
    # Minimizing leaves just the tray icon. Off by default: a window gone from the taskbar,
    # with its icon tucked under the tray's ^, looks like Horu quit.
    "minimizeToTray": False,
    "autoTranslate": True,  # one AI request per new dialogue line (once an AI provider is set up)
    # Use the AI's reading of a possibly misread line (or name tag) straight away, showing
    # what changed, instead of asking "Did you mean ...?".
    "applyAiReadings": True,
    # Have a vision model read each new dialogue line from its screenshot, to catch OCR
    # misreads: one AI request, with a picture of the line, per line.
    "aiChecks": True,
    "blurTranslations": True,
    "learnerLevel": "Beginner (studying with Genki I)",
    # AI provider chosen in the settings panel ("" = use config.toml). Keys are not stored
    # here; they go to the system credential store.
    "aiProvider": "",
    "aiModel": "",
    "aiBaseUrl": "",
    "translationModel": "",  # "" = same model as explanations
    "ankiDeck": "Horu",
    # How Horu reaches Anki: "web" (AnkiWeb, no Anki app needed) or "connect" (Anki open on
    # this PC, with the AnkiConnect add-on).
    "ankiVia": "connect",
    # Run the flashcard agent on every new dialogue line, adding words not yet in the deck.
    "autoCards": False,
    # Read the game's dialogue box by itself whenever its text changes.
    "autoCapture": False,
    # The capture hotkey, e.g. "<ctrl>+<alt>+j"; "" uses config.toml's.
    "captureHotkey": "",
    # Lines from a text hooker (Textractor, LunaHook...): "" off, "clipboard", or "websocket".
    "hookSource": "",
    "hookUrl": "ws://127.0.0.1:6677",
    # Look: colours ("horu", "indigo", "matcha") and light or dark ("system" follows Windows).
    "theme": "horu",
    "colorMode": "system",
    # Where saved words go: "" not chosen yet, "horu" (the Study tab), or "anki".
    "studyWith": "",
    "newPerDay": 10,  # new words started in the Study tab each day
    "welcomeDone": False,  # the first-start screen was finished or skipped
}


class Prefs:
    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        self.path = path
        # pywebview runs each JavaScript API call on its own thread.
        self._lock = threading.Lock()
        self.values = {**DEFAULTS, **self._read()}

    def set(self, key: str, value) -> None:
        if key not in DEFAULTS:
            raise KeyError(f"Unknown preference: {key}")
        with self._lock:
            self.values[key] = type(DEFAULTS[key])(value)
            self._write()

    def update(self, values: dict) -> None:
        """Set several preferences with a single write."""
        unknown = set(values) - set(DEFAULTS)
        if unknown:
            raise KeyError(f"Unknown preferences: {', '.join(sorted(unknown))}")
        with self._lock:
            for key, value in values.items():
                self.values[key] = type(DEFAULTS[key])(value)
            self._write()

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        values = {}
        for key, value in data.items():
            if key in DEFAULTS:
                try:
                    values[key] = type(DEFAULTS[key])(value)
                except (TypeError, ValueError):
                    pass  # ignore a corrupted value; the default applies
        return values

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.values, indent=2), encoding="utf-8")
        tmp.replace(self.path)
