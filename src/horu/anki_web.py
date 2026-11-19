"""Anki without the Anki app: cards go into a local copy of the learner's collection, which
syncs with AnkiWeb (through fastanki, https://github.com/AnswerDotAI/fastanki). Reviews happen
on ankiweb.net or in AnkiDroid/AnkiMobile; a desktop Anki on the same account gets the cards
when it syncs.

The learner signs in once: the AnkiWeb password is only sent to AnkiWeb, and only the sync key it
returns is kept (in the operating system's credential store, like API keys).

Screenshots go inside the card, as a small JPEG in the field (Anki, AnkiWeb, and the phone
apps all show it), not as a media file: syncing media means downloading every file on the
account first, which for a deck with audio is gigabytes.

Syncing: right after cards are added (so the local copy rarely holds anything AnkiWeb lacks),
and when Horu asks for due counts (at most once a minute). When AnkiWeb says the collections have diverged too far to
merge (a "full sync", e.g. after a note type was edited in desktop Anki), AnkiWeb's copy wins:
cards Horu added that hadn't synced yet are added again on top of it.
"""

from __future__ import annotations

import html
import json
import threading
import time
from pathlib import Path

from horu import anki
from horu.anki import AnkiError, Card

DEFAULT_DIR = Path("data/anki")
ANKIWEB_DECKS = "https://ankiweb.net/decks"
KEYRING_SERVICE = "Horu"
_KEY_NAME = "ankiweb-sync-key"
_COUNTS_SYNC_SECONDS = 60
# Screenshots in cards: this wide at most, as JPEG at this quality (about 40 KB each).
_SHOT_WIDTH, _SHOT_QUALITY = 960, 80  # the whole scene: wide enough to read its dialogue


class AnkiWebClient:
    def __init__(self, folder: Path = DEFAULT_DIR, keys=None, endpoint: str | None = None) -> None:
        self.folder = folder
        self.endpoint = endpoint  # None: AnkiWeb (another sync server, for tests)
        self._keys = keys or _Keyring()
        self._lock = threading.RLock()
        self._sync_timer: threading.Timer | None = None
        self.last_sync: float | None = None
        self.last_error: str | None = None

    # --- account ---------------------------------------------------------------------

    @property
    def signed_in(self) -> bool:
        return bool(self._keys.get())

    @property
    def account(self) -> str:
        return (self._settings().get("email") or "") if self.signed_in else ""

    def sign_in(self, email: str, password: str) -> dict:
        """Log in to AnkiWeb and download the collection. Returns status()."""
        from fastanki import SyncServer

        server = SyncServer(self.endpoint)
        try:
            server.login(email.strip(), password)
        except Exception as e:
            raise AnkiError(_sync_message(e, signing_in=True)) from e
        self._keys.set(json.dumps({"hkey": server.hkey, "endpoint": server.endpoint}))
        self._save_settings(email=email.strip())
        self.sync()
        return self.status()

    def sign_out(self) -> None:
        self._keys.delete()

    # --- what the rest of Horu uses ---------------------------------------------------

    def status(self) -> dict:
        if not self.signed_in:
            return {"connected": False, "decks": [], "error": "Sign in to AnkiWeb in Settings → Study.", "via": "web"}
        with self._open() as col:
            decks = sorted(d for d in col.decks() if d != "Default" or len(col.decks()) == 1)
        return {"connected": True, "decks": decks, "via": "web", "account": self.account, "lastSync": self.last_sync,
                **({"error": self.last_error} if self.last_error else {})}

    def has_word(self, deck: str, word: str) -> bool:
        with self._open() as col:
            return _has_word(col, deck, word)

    def due_counts(self, deck: str) -> dict:
        """{"due", "new"} after a sync, so reviews done on AnkiWeb or a phone count."""
        if self.last_sync is None or time.time() - self.last_sync > _COUNTS_SYNC_SECONDS:
            try:
                self.sync()
            except AnkiError:
                pass  # offline: the local copy's counts
        with self._open() as col:
            new, learning, review = col.due_counts(deck)
        return {"due": learning + review, "new": new}

    def review_deck(self, deck: str) -> None:
        import webbrowser

        webbrowser.open(ANKIWEB_DECKS)

    def add_card(self, deck: str, card: Card, screenshot_png: bytes | None = None) -> int:
        if not self.signed_in:
            raise AnkiError("Sign in to AnkiWeb in Settings → Study to add cards.")
        with self._lock, self._open() as col:
            _ensure_note_type(col)
            if _has_word(col, deck, card.dictionary_form):
                raise AnkiError(f"{card.dictionary_form} is already in your {deck} deck.")
            screenshot = f'<img src="data:image/jpeg;base64,{_small_jpeg(screenshot_png)}">' if screenshot_png else ""
            note = col.add(model=anki.NOTE_TYPE, deck=deck, tags=["horu"], **anki.note_fields(card, screenshot))
        self.sync_soon()
        return note.id

    # --- syncing ---------------------------------------------------------------------

    def sync_soon(self, delay: float = 3.0) -> None:
        """Sync in a moment, once a burst of new cards is in."""
        with self._lock:
            if self._sync_timer:
                self._sync_timer.cancel()
            self._sync_timer = threading.Timer(delay, self._sync_quietly)
            self._sync_timer.daemon = True
            self._sync_timer.start()

    def _sync_quietly(self) -> None:
        try:
            self.sync()
        except AnkiError:
            pass  # kept in last_error; the next sync tries again

    def sync(self) -> str:
        """Sync with AnkiWeb: notes and media. Returns "noChanges", "success", or "full sync"."""
        from fastanki import FullSyncRequired, SyncServer

        stored = self._keys.get()
        if not stored:
            raise AnkiError("Sign in to AnkiWeb in Settings → Study.")
        auth = json.loads(stored)
        server = SyncServer(auth.get("endpoint"), auth["hkey"])
        with self._lock, self._open() as col:
            try:
                try:
                    result = col.sync_collection(server)
                except FullSyncRequired as e:
                    local, remote = e.args
                    if remote["empty"] and not local["empty"]:
                        col.full_upload(server)
                    else:
                        pending = _unsynced_horu_notes(col)
                        col.full_download(server)
                        for deck, tags, fields in pending:
                            col.add(model=anki.NOTE_TYPE, deck=deck, tags=tags, **fields)
                        if pending:
                            col.sync_collection(server)
                    result = "full sync"
            except Exception as e:
                self.last_error = _sync_message(e)
                raise AnkiError(self.last_error) from e
        if server.endpoint != auth.get("endpoint"):  # AnkiWeb moved the account to another server
            self._keys.set(json.dumps({"hkey": server.hkey, "endpoint": server.endpoint}))
        self.last_sync, self.last_error = time.time(), None
        return result

    # --- storage ---------------------------------------------------------------------

    def _open(self):
        from fastanki import Collection

        self.folder.mkdir(parents=True, exist_ok=True)
        return Collection.open(self.folder / "collection.anki2")

    def _settings(self) -> dict:
        try:
            return json.loads((self.folder / "account.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_settings(self, **values) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / "account.json").write_text(json.dumps({**self._settings(), **values}), encoding="utf-8")


class _Keyring:
    """The AnkiWeb sync key, in the operating system's credential store."""

    def get(self) -> str:
        try:
            import keyring

            return keyring.get_password(KEYRING_SERVICE, _KEY_NAME) or ""
        except Exception:
            return ""

    def set(self, value: str) -> None:
        import keyring

        keyring.set_password(KEYRING_SERVICE, _KEY_NAME, value)

    def delete(self) -> None:
        try:
            import keyring

            keyring.delete_password(KEYRING_SERVICE, _KEY_NAME)
        except Exception:
            pass


def _ensure_note_type(col) -> None:
    """Horu's note type, made the way Anki makes one: new (usn -1), so it syncs to AnkiWeb."""
    if anki.NOTE_TYPE in col.notetypes():
        _update_note_type(col)
        return
    from fastanki.schema import add_notetype, field_cfg, notetype_cfg, template_cfg

    with col._tx():
        ntid = add_notetype(
            col.con, anki.NOTE_TYPE,
            [(name, field_cfg()) for name in anki.FIELDS],
            [("Sentence", template_cfg(anki.FRONT, anki.BACK))],
            notetype_cfg(css=anki.CSS, reqs=[(0, "any", [0, 1])]),
        )
        now = int(time.time())
        col.con.execute("update notetypes set mtime_secs=?, usn=-1 where id=?", (now, ntid))
        col.con.execute("update templates set mtime_secs=?, usn=-1 where ntid=?", (now, ntid))
        col._dirty()


def _update_note_type(col) -> None:
    """Give a note type that still has Horu's earlier card front the current one (the word,
    then its line), marked changed so it syncs. A front edited in Anki is left alone."""
    from fastanki._proto import notetypes_pb2

    ntid = col.q1("select id from notetypes where name=?", anki.NOTE_TYPE)
    rows = col.con.execute("select ord, config from templates where ntid=?", (ntid,)).fetchall()
    changed = []
    for ord_, blob in rows:
        config = notetypes_pb2.Notetype.Template.Config()
        config.ParseFromString(blob)
        if config.q_format.strip() == anki.OLD_FRONT:
            config.q_format, config.a_format = anki.FRONT, anki.BACK
            changed.append((config.SerializeToString(), ord_))
    if not changed:
        return
    with col._tx():
        now = int(time.time())
        for blob, ord_ in changed:
            col.con.execute("update templates set config=?, mtime_secs=?, usn=-1 where ntid=? and ord=?", (blob, now, ntid, ord_))
        config = notetypes_pb2.Notetype.Config()
        config.ParseFromString(col.q1("select config from notetypes where id=?", ntid))
        config.css = anki.CSS
        col.con.execute("update notetypes set config=?, mtime_secs=?, usn=-1 where id=?", (config.SerializeToString(), now, ntid))
        col._dirty()


def _small_jpeg(png: bytes) -> str:
    """A screenshot as base64 JPEG, small enough to sit in a note field."""
    import base64
    import io

    from PIL import Image

    image = Image.open(io.BytesIO(png)).convert("RGB")
    if image.width > _SHOT_WIDTH:
        image = image.resize((_SHOT_WIDTH, round(image.height * _SHOT_WIDTH / image.width)))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=_SHOT_QUALITY)
    return base64.b64encode(buffer.getvalue()).decode()


def _has_word(col, deck: str, word: str) -> bool:
    """Whether a Horu card for this dictionary form is in the deck."""
    if anki.NOTE_TYPE not in col.notetypes():
        return False
    escaped = html.escape(word)
    return any(n.nt.name == anki.NOTE_TYPE and n["Word"] == escaped for n in col.find_notes(deck=deck, Word=escaped))


def _unsynced_horu_notes(col) -> list[tuple[str, list[str], dict]]:
    """Horu notes added since the last sync: (deck, tags, fields), to add again after AnkiWeb's
    copy replaces this one."""
    if anki.NOTE_TYPE not in col.notetypes():
        return []
    found = []
    for note in col.find_notes(where="n.usn=-1"):
        if note.nt.name != anki.NOTE_TYPE:
            continue
        deck_id = col.q1("select did from cards where nid=? limit 1", note.id)
        deck = (col.q1("select name from decks where id=?", deck_id) or "Horu").replace("\x1f", "::")
        found.append((deck, [t for t in note.tags if t], dict(note.fields)))
    return found


def _sync_message(error: Exception, signing_in: bool = False) -> str:
    text = str(error)
    if signing_in and ("403" in text or "401" in text or "nvalid" in text):
        return "AnkiWeb didn't accept that email and password."
    if "ConnectError" in type(error).__name__ or "connect" in text.lower():
        return "Couldn't reach AnkiWeb. Check your internet connection."
    return f"AnkiWeb sync failed: {text[:200]}"
