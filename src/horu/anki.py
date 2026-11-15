"""Adding flashcards to Anki through the AnkiConnect add-on (https://ankiweb.net/shared/info/2055492159).

AnkiConnect runs a small local web server inside Anki while it's open. Horu adds notes of its
own type, "Horu Sentence", which it creates on first use: the word on the front, with the
line from the game it came from under it; reading, meaning, grammar notes, translation, and a
screenshot of the line on the back.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import urllib.error
import urllib.request
from dataclasses import dataclass

ANKICONNECT_URL = "http://127.0.0.1:8765"
ANKICONNECT_CODE = "2055492159"
NOTE_TYPE = "Horu Sentence"
FIELDS = ["Sentence", "Word", "Reading", "Meaning", "Notes", "Translation", "Screenshot", "Source"]

# The front shows the word, then the line it came from under it. The screenshot is on the back.
FRONT = """<div class="word">{{Word}}</div>
<div class="sentence">{{Sentence}}</div>"""
BACK = """{{FrontSide}}
<hr id="answer">
<div class="reading">{{Reading}}</div>
<div class="meaning">{{Meaning}}</div>
{{#Notes}}<div class="notes">{{Notes}}</div>{{/Notes}}
{{#Translation}}<div class="translation">{{Translation}}</div>{{/Translation}}
<div class="screenshot">{{Screenshot}}</div>
<div class="source">{{Source}}</div>"""
CSS = """.card { font-family: "Yu Gothic UI", "Hiragino Sans", "Noto Sans JP", sans-serif; font-size: 22px;
  text-align: center; color: #222; background: #fafafa; }
.word { font-size: 44px; line-height: 1.3; }
.sentence { margin-top: 14px; font-size: 22px; line-height: 1.6; color: #555; }
.sentence b { font-weight: inherit; color: inherit; }
.reading { font-size: 24px; }
.meaning { margin-top: 6px; }
.notes, .translation, .source { margin-top: 10px; font-size: 16px; color: #555; }
.screenshot img { max-width: 100%; margin-top: 12px; border-radius: 6px; }
.nightMode .card, .card.nightMode { color: #eee; background: #1e1e1e; }
.nightMode .sentence, .nightMode .notes, .nightMode .translation, .nightMode .source { color: #aaa; }"""
# The front of Horu's cards before 0.1's release (the line, its word in red). A note type still
# using it is brought up to date; one whose front was edited in Anki is left as its owner made it.
OLD_FRONT = """<div class="sentence">{{Sentence}}</div>"""


class AnkiError(Exception):
    """A failure to show the user as-is."""


@dataclass
class Card:
    sentence: str
    word: str  # as it appears in the sentence
    dictionary_form: str
    reading: str
    meaning: str
    notes: str = ""
    translation: str = ""
    source: str = ""


class AnkiClient:
    def __init__(self, url: str = ANKICONNECT_URL, timeout: float = 5.0) -> None:
        self.url = url
        self.timeout = timeout

    def call(self, action: str, **params):
        request = urllib.request.Request(
            self.url,
            data=json.dumps({"action": action, "version": 6, "params": params}).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                reply = json.load(response)
        except (urllib.error.URLError, OSError) as e:
            raise AnkiError(
                f"Couldn't reach Anki. Open Anki, and make sure the AnkiConnect add-on (code {ANKICONNECT_CODE}) is installed."
            ) from e
        if reply.get("error"):
            raise AnkiError(f"Anki: {reply['error']}")
        return reply.get("result")

    def status(self) -> dict:
        try:
            return {"connected": True, "decks": sorted(self.call("deckNames"))}
        except AnkiError as e:
            return {"connected": False, "decks": [], "error": str(e)}

    def has_word(self, deck: str, word: str) -> bool:
        """Whether a Horu card for this dictionary form already exists in the deck."""
        query = f'"deck:{_quote(deck)}" "note:{NOTE_TYPE}" "Word:{_quote(word)}"'
        return bool(self.call("findNotes", query=query))

    def due_counts(self, deck: str) -> dict:
        """{"due", "new"}: cards in the deck due for review today, and cards not yet studied."""
        due = self.call("findCards", query=f'"deck:{_quote(deck)}" is:due')
        new = self.call("findCards", query=f'"deck:{_quote(deck)}" is:new')
        return {"due": len(due), "new": len(new)}

    def review_deck(self, deck: str) -> None:
        """Bring Anki to the front, reviewing the deck."""
        self.call("guiDeckReview", name=deck)

    def add_card(self, deck: str, card: Card, screenshot_png: bytes | None = None) -> int:
        """Add a card and return its note id. Refuses duplicates within the deck."""
        self._ensure_note_type()
        self.call("createDeck", deck=deck)
        if self.has_word(deck, card.dictionary_form):
            raise AnkiError(f"{card.dictionary_form} is already in your {deck} deck.")
        screenshot = ""
        if screenshot_png:
            filename = self.call(
                "storeMediaFile",
                filename=f"horu_{hashlib.sha1((card.sentence + card.word).encode()).hexdigest()[:12]}.png",
                data=base64.b64encode(screenshot_png).decode(),
            )
            screenshot = f'<img src="{html.escape(filename)}">'
        note = {
            "deckName": deck,
            "modelName": NOTE_TYPE,
            "fields": note_fields(card, screenshot),
            "tags": ["horu"],
            "options": {"allowDuplicate": False, "duplicateScope": "deck"},
        }
        return self.call("addNote", note=note)

    def _ensure_note_type(self) -> None:
        if NOTE_TYPE in self.call("modelNames"):
            templates = self.call("modelTemplates", modelName=NOTE_TYPE) or {}
            if any(t.get("Front", "").strip() == OLD_FRONT for t in templates.values()):
                update = {name: {"Front": FRONT, "Back": BACK} for name in templates}
                self.call("updateModelTemplates", model={"name": NOTE_TYPE, "templates": update})
                self.call("updateModelStyling", model={"name": NOTE_TYPE, "css": CSS})
            return
        self.call(
            "createModel",
            modelName=NOTE_TYPE,
            inOrderFields=FIELDS,
            css=CSS,
            isCloze=False,
            cardTemplates=[{"Name": "Sentence", "Front": FRONT, "Back": BACK}],
        )


def note_fields(card: Card, screenshot: str = "") -> dict:
    """The "Horu Sentence" fields for a card, as HTML. `screenshot` is an <img> tag or ""."""
    return {
        "Sentence": html.escape(card.sentence),
        "Word": html.escape(card.dictionary_form),
        "Reading": html.escape(card.reading),
        "Meaning": html.escape(card.meaning),
        "Notes": html.escape(card.notes),
        "Translation": html.escape(card.translation),
        "Screenshot": screenshot,
        "Source": html.escape(card.source),
    }


def _quote(text: str) -> str:
    """Escape a value for an Anki search query."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("*", "\\*").replace("_", "\\_")
