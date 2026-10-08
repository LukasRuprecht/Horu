"""The flashcard agent: an AI model that turns a game line into Anki cards using tools.

Given a line, the model decides which words are worth learning for this learner, checks which
are already in their Anki deck, looks the rest up in Horu's dictionary, and writes the cards
itself, with a meaning and note that fit the scene. It works with any provider that supports
tool calling (Claude, OpenAI-compatible APIs, Ollama models with tool support).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from horu import anki
from horu.llm.tools import Tool
from horu.nlp import analyze

if TYPE_CHECKING:
    from horu.app import App

MAX_CARDS = 3
_RECENT_LINES = 8

SYSTEM = """\
You are Horu, a Japanese tutor built into a tool that reads text from video games while the \
learner plays. You make Anki flashcards from lines the learner has just seen, using your \
tools. The learner's level: {level}.

Write meanings and notes in English, short enough for a flashcard. The game text was \
captured with OCR and may contain a misread character; if a word looks misread, skip it."""

TASK = """\
Make flashcards from this line{speaker}:
「{line}」

1. Pick up to {limit} words from it that are most worth learning at the learner's level. \
Skip particles, names, and words a learner at this level surely knows already.
2. Use check_deck to see which of them are already in their Anki deck, and don't add those.
3. For each remaining word, use lookup_word, then add_flashcard with a short meaning that \
fits this line and a one-line note on how it's used here. Use get_scene_context if the \
earlier dialogue would change the meaning.
4. Finish with a short summary (two or three lines) of what you added, what you skipped, \
and why."""


NUDGE = """You haven't added any cards yet. If any words are worth learning, call the add_flashcard tool now, once per word, rather than describing the calls. If none are worth adding, say so in one line."""


@dataclass
class CardResult:
    summary: str  # the agent's own account of what it did
    added: list[str]  # dictionary forms of the cards actually added


def make_cards(app: App, line_id: int, on_step: Callable[[str], None]) -> CardResult:
    """Run the agent on one line. Raises llm.AIError on failure."""
    line = app.lines[line_id]
    speaker = line["speaker"]["text"] if line["speaker"] else None
    attempts: list[str] = []  # words the model tried to add, whether or not they went in
    added: list[str] = []
    tools = card_tools(app, line_id, on_step, attempts, added)
    system = SYSTEM.format(level=app.prefs.values["learnerLevel"])
    task = TASK.format(line=line["text"], speaker=f" (spoken by {speaker})" if speaker else "", limit=MAX_CARDS)
    nudged = False

    def follow_up(answer: str) -> str | None:
        # Small models often describe the add_flashcard calls instead of making them. One
        # reminder, only if no card was even attempted.
        nonlocal nudged
        if attempts or nudged:
            return None
        nudged = True
        return NUDGE

    summary = app._provider.run_tools(system, task, tools, follow_up=follow_up)
    return CardResult(summary, added)


def card_tools(
    app: App,
    line_id: int,
    on_step: Callable[[str], None],
    attempts: list[str] | None = None,
    added: list[str] | None = None,
) -> list[Tool]:
    line = app.lines[line_id]
    deck = app.prefs.values["ankiDeck"] or "Horu"
    added = [] if added is None else added
    attempts = [] if attempts is None else attempts

    def lookup_word(args: dict) -> str:
        on_step(f"Looking up {args['word']}")
        words = [w for w in analyze(args["word"]) if w.pos != "symbol"]
        if not words:
            return "Nothing to look up."
        word = words[0].to_dict()
        found = app.lookup(word)
        return json.dumps(
            {
                "dictionary_form": word["dictionary_form"],
                "reading": word["dictionary_reading"],
                "part_of_speech": word["pos"],
                "grammar_in_this_form": word["grammar"],
                "dictionary_meanings": [
                    {"headword": (e["kanji"] or e["readings"])[0], "senses": [s["gloss"][:4] for s in e["senses"][:4]]}
                    for e in found["entries"]
                ],
                "name_readings": [n["names"] for n in found["names"]],
            },
            ensure_ascii=False,
        )

    def get_scene_context(args: dict) -> str:
        on_step("Reading the scene")
        context = app._line_context(line_id)
        previous = context.previous or ["(no earlier dialogue captured)"]
        return "Earlier lines, oldest first:\n" + "\n".join(previous) + f"\n\nCurrent line:\n{context.quoted()}"

    def check_deck(args: dict) -> str:
        on_step("Checking your Anki deck")
        return json.dumps({w: app.anki.has_word(deck, w) for w in args["words"]}, ensure_ascii=False)

    def add_flashcard(args: dict) -> str:
        attempts.append(args["word"])
        if len(added) >= MAX_CARDS:
            return f"Not added: the limit is {MAX_CARDS} cards per line."
        on_step(f"Adding {args['word']}")
        card = _card_for_word(app, line_id, args["word"])
        card.meaning = args["meaning"].strip() or card.meaning
        card.notes = "; ".join(filter(None, [args["note"].strip(), card.notes]))
        try:
            app.anki.add_card(deck, card, app._screenshots.get(line_id))
        except anki.AnkiError as e:
            return f"Not added: {e}"
        added.append(card.dictionary_form)
        return f"Added {card.dictionary_form} to the {deck} deck."

    def schema(properties: dict) -> dict:
        return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}

    word_text = {"type": "string", "description": "The word as it appears in the line, or its dictionary form."}
    return [
        Tool(
            "lookup_word",
            "Look a Japanese word up in Horu's dictionary: dictionary form, reading, part of speech, the "
            "conjugation it's in, and JMdict meanings.",
            schema({"word": word_text}),
            lookup_word,
        ),
        Tool(
            "get_scene_context",
            "Get the dialogue lines the learner saw just before this one, with speakers.",
            schema({}),
            get_scene_context,
        ),
        Tool(
            "check_deck",
            "Check which words (dictionary forms) are already in the learner's Anki deck.",
            schema({"words": {"type": "array", "items": {"type": "string"}, "description": "Dictionary forms to check."}}),
            check_deck,
        ),
        Tool(
            "add_flashcard",
            "Add a flashcard for one word of the current line to the learner's Anki deck. The card also gets "
            "the full line, the reading, Horu's grammar notes, the translation if there is one, and a "
            "screenshot of the scene.",
            schema(
                {
                    "word": word_text,
                    "meaning": {"type": "string", "description": "A short English meaning that fits this line."},
                    "note": {"type": "string", "description": "One line on how the word is used here."},
                }
            ),
            add_flashcard,
        ),
    ]


def _card_for_word(app: App, line_id: int, word: str) -> anki.Card:
    """A card for `word` in the line: matched to the line's own words where possible, so the
    card gets Horu's analysis (reading, grammar) for the form actually used."""
    line = app.lines[line_id]
    for index, w in enumerate(line["words"]):
        if word in (w["surface"], w["dictionary_form"]):
            return app.card_for(line_id, index)
    # A word the line's tokenization split differently (逃げ切る is 逃げ + 切る): keep the
    # whole word the model chose, rather than just its first piece.
    analysed = [w for w in analyze(word) if w.pos != "symbol"]
    if len(analysed) == 1:
        info = analysed[0].to_dict()
        dictionary_form, reading, notes = info["dictionary_form"], info["dictionary_reading"], ", ".join(info["grammar"])
    else:
        dictionary_form, reading, notes = word, "".join(w.reading for w in analysed), ""
    return anki.Card(
        sentence=line["text"],
        word=word,
        dictionary_form=dictionary_form,
        reading=reading,
        meaning="",
        notes=notes,
        translation=line.get("translation", ""),
        source=line["game"].removesuffix(".exe"),
    )
