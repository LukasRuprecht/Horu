"""What Horu's Anki cards are made of (anki_web.py puts them in the learner's collection).

Horu adds notes of its own type, "Horu Sentence", which it creates on first use: the word on
the front, with the
line from the game it came from under it; reading, meaning, grammar notes, translation, and a
screenshot of the scene on the back.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

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

