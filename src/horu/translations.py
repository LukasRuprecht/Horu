"""Translations Horu has already made, per game, so a line seen again costs no AI request.

Games repeat themselves: shop greetings, NPC chatter, menus, a scene replayed after a game
over. A translation is kept under the game, speaker, and exact text, with whether that text
is trustworthy ("verified": OCR was sure of it, or an AI that read the screenshot agreed). A
line OCR was unsure of only reuses a verified translation; otherwise it goes to the AI with
its screenshot like any new line.

Translating a line again (Retry, or after an edit) replaces what's kept.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

DEFAULT_PATH = Path("data/translations.db")


class TranslationMemory:
    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        self.path = path
        self._db: sqlite3.Connection | None = None  # opened when first used
        self._lock = threading.Lock()

    def _connection(self) -> sqlite3.Connection:
        if self._db is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(self.path, check_same_thread=False)
            with self._db:
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS translations (game TEXT, speaker TEXT, text TEXT, translation TEXT,"
                    " verified INTEGER, PRIMARY KEY (game, speaker, text))"
                )
        return self._db

    def get(self, game: str, speaker: str | None, text: str) -> tuple[str, bool] | None:
        """(translation, verified) for this line, if it was translated before."""
        with self._lock:
            row = self._connection().execute(
                "SELECT translation, verified FROM translations WHERE game = ? AND speaker = ? AND text = ?",
                (game, speaker or "", text),
            ).fetchone()
        return (row[0], bool(row[1])) if row else None

    def put(self, game: str, speaker: str | None, text: str, translation: str, verified: bool) -> None:
        if not translation.strip():
            return
        with self._lock:
            db = self._connection()
            with db:
                # Once the text is verified, it stays verified.
                db.execute(
                    "INSERT INTO translations VALUES (?, ?, ?, ?, ?) ON CONFLICT (game, speaker, text) DO UPDATE"
                    " SET translation = excluded.translation, verified = max(verified, excluded.verified)",
                    (game, speaker or "", text, translation, int(verified)),
                )
