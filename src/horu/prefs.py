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
    # game. Off for people who stream or share their screen with Horu visible.
    "hideFromCapture": True,
    "autoTranslate": False,  # one AI request per new dialogue line
    "blurTranslations": True,
    "learnerLevel": "Beginner (studying with Genki I)",
    # AI provider chosen in the settings panel ("" = use config.toml). Keys are not stored
    # here; they go to the system credential store.
    "aiProvider": "",
    "aiModel": "",
    "aiBaseUrl": "",
    "translationModel": "",  # "" = same model as explanations
    "ankiDeck": "Horu",
    # Run the flashcard agent on every new dialogue line, adding words not yet in the deck.
    "autoCards": False,
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
