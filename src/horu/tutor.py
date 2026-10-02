"""What Horu asks the AI: translations, word explanations, and line breakdowns.

Each task starts a fresh chat whose first message carries the game line and its context;
follow-up questions continue the same chat.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

from horu.llm import Chat, Provider

SYSTEM = """\
You are Horu, a Japanese tutor built into a tool that reads text from video games while \
the learner plays. The learner pauses mid-game to read your answer, so be brief and \
scannable: lead with what they need, skip filler and pleasantries.

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
    def __init__(self, provider: Provider, level: str) -> None:
        self.provider = provider
        self.level = level

    def _chat(self, effort: str = "low") -> Chat:
        return self.provider.chat(SYSTEM.format(level=self.level), effort=effort)

    def translate(self, line: Line) -> Iterator[str]:
        return self._chat().send(TRANSLATE.format(context=line.context(), line=line.quoted()))

    def explain_word(self, line: Line, word: dict) -> tuple[Chat, Iterator[str]]:
        chat = self._chat()
        prompt = EXPLAIN_WORD.format(
            context=line.context(), line=line.quoted(), surface=word["surface"], analysis=_word_analysis(word)
        )
        return chat, chat.send(prompt)

    def explain_line(self, line: Line) -> tuple[Chat, Iterator[str]]:
        chat = self._chat()
        return chat, chat.send(EXPLAIN_LINE.format(context=line.context(), line=line.quoted()))


def _word_analysis(word: dict) -> str:
    """What the tokenizer already worked out, so the model can confirm or correct it."""
    parts = []
    if word.get("dictionary_form") and word["dictionary_form"] != word["surface"]:
        parts.append(f"dictionary form {word['dictionary_form']}")
    if word.get("grammar"):
        parts.append("forms: " + ", ".join(word["grammar"]))
    return f" (my tokenizer says: {'; '.join(parts)})" if parts else ""
