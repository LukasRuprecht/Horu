"""What Horu asks the AI: translations, word explanations, and line breakdowns.

Each task starts a fresh chat whose first message carries the game line and its context;
follow-up questions continue the same chat.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Iterator

from horu.llm import Chat, Provider

SYSTEM = """\
You are Horu, a Japanese tutor built into a tool that reads text from video games while \
the learner plays. Answer in English; quote the Japanese you're explaining. The learner \
pauses mid-game to read your answer, so be brief and scannable: lead with what they need, \
skip filler and pleasantries.

The learner's level: {level}. Pitch explanations to that level. When a grammar point is \
probably beyond it, say so in a few words rather than teaching it in full.

Formatting: plain sentences and short "- " bullet lists; **bold** for the key word or \
form. No headings and no tables. Write Japanese in Japanese script, never romaji; give \
the reading in parentheses after kanji the learner may not know, e.g. 遅刻（ちこく）.

The game text was captured with OCR and may contain misread characters. If something \
looks like a misreading, say what the text most likely is and explain that."""

TRANSLATE = """\
{context}Translate this line into natural English that fits the scene:
{line}

Reply with only the translation: no quotes, notes, or romaji."""

# Text without a speaker may be narration, but is often help, menu, or tutorial text, which
# shouldn't be rewritten as speech.
TRANSLATE_TEXT = """\
Translate this text from a video game into natural English. It may be narration, or help, \
menu, or tutorial text; keep its tone, and keep any line breaks and lists.

{text}

Reply with only the translation: no quotes, notes, or romaji."""

# For a dedicated translation model (e.g. Google's Japanese-English Gemma fine-tune): no
# system prompt, so it keeps the instruction it was built with, and the task given as its own
# turn, acknowledged, before the text. Sent as one message, the 2B Gemma translator often
# replies just "OK" instead of translating.
TRANSLATE_ONLY_SETUP = [
    {"role": "user", "content": "Translate Japanese to English."},
    {"role": "assistant", "content": "OK"},
]

SPEAKER = """\
This is the speaker label shown above a line of video game dialogue: {text}
It may be a character's name or a description such as 謎の通信 or 少年のような声. Translate it \
into short, natural English as an English release of the game would show it: names in \
romaji, descriptions translated. Reply with only the English."""

CARD_NOTE = """\
{context}Line from the game:
{line}

Write the back of a flashcard for 「{surface}」 as it's used in this line{analysis}.
Reply with only this JSON:
{{"meaning": "<a short English meaning that fits this line>", "note": "<one line on how it's used here>"}}"""

EXPLAIN_WORD = """\
{context}Current line:
{line}

Explain 「{surface}」 as it's used in this line{analysis}. Cover what it means here, how \
the form is built if it's conjugated, and any nuance. Finish with a one-line natural \
translation of the whole line."""

EXPLAIN_LINE = """\
{context}Current line:
{line}

Break this line down for me: go through its words and grammar points in order, briefly, \
then give a natural translation."""


@dataclass
class Line:
    text: str
    speaker: str | None = None
    previous: list[str] = field(default_factory=list)  # earlier lines, oldest first, for context

    def quoted(self) -> str:
        return f"{self.speaker}「{self.text}」" if self.speaker else f"「{self.text}」"

    def context(self) -> str:
        if not self.previous:
            return ""
        return "Earlier lines, for context:\n" + "\n".join(self.previous) + "\n\n"


class Tutor:
    def __init__(self, provider: Provider, level: str, translator: Provider | None = None) -> None:
        self.provider = provider
        self.level = level
        self.translator = translator

    def _chat(self, effort: str = "low", temperature: float | None = None) -> Chat:
        return self.provider.chat(SYSTEM.format(level=self.level), effort=effort, temperature=temperature)

    def translate(self, line: Line) -> Iterator[str]:
        # Temperature 0 where supported: the same line should get the same translation.
        if self.translator:
            return self.translator.chat("", history=TRANSLATE_ONLY_SETUP, temperature=0).send(line.text)
        if line.speaker is None and not line.previous:
            return self._chat(temperature=0).send(TRANSLATE_TEXT.format(text=line.text))
        return self._chat(temperature=0).send(TRANSLATE.format(context=line.context(), line=line.quoted()))

    def translate_speaker(self, text: str) -> str:
        """Short English for a speaker label, e.g. 謎の通信 -> "Mysterious transmission"."""
        if self.translator:
            chunks = self.translator.chat("", history=TRANSLATE_ONLY_SETUP, temperature=0).send(text)
        else:
            chunks = self.provider.chat("", temperature=0).send(SPEAKER.format(text=text))
        return _clean_label("".join(chunks))

    def card_note(self, line: Line, word: dict) -> tuple[str, str]:
        """A meaning and usage note for one word that fit this line, for its flashcard.
        Returns ("", "") if the reply can't be understood."""
        prompt = CARD_NOTE.format(
            context=line.context(), line=line.quoted(), surface=word["surface"], analysis=_word_analysis(word)
        )
        reply = "".join(self._chat(temperature=0).send(prompt))
        match = re.search(r"\{.*\}", reply, re.S)
        try:
            data = json.loads(match.group(0)) if match else {}
        except ValueError:
            data = {}
        return str(data.get("meaning", "")).strip(), str(data.get("note", "")).strip()

    def explain_word(self, line: Line, word: dict) -> tuple[Chat, Iterator[str]]:
        chat = self._chat()
        prompt = EXPLAIN_WORD.format(
            context=line.context(), line=line.quoted(), surface=word["surface"], analysis=_word_analysis(word)
        )
        return chat, chat.send(prompt)

    def explain_line(self, line: Line) -> tuple[Chat, Iterator[str]]:
        chat = self._chat()
        return chat, chat.send(EXPLAIN_LINE.format(context=line.context(), line=line.quoted()))


def _clean_label(reply: str) -> str:
    """First line of a reply, without quotes or a trailing full stop."""
    first = reply.strip().splitlines()[0] if reply.strip() else ""
    return first.strip().strip("\"'「」『』").rstrip(".。").strip()[:60]


def _word_analysis(word: dict) -> str:
    """What the tokenizer already worked out, so the model can confirm or correct it."""
    parts = []
    if word.get("dictionary_form") and word["dictionary_form"] != word["surface"]:
        parts.append(f"dictionary form {word['dictionary_form']}")
    if word.get("grammar"):
        parts.append("forms: " + ", ".join(word["grammar"]))
    return f" (Horu's analyzer, which is reliable on conjugations, says: {'; '.join(parts)})" if parts else ""
