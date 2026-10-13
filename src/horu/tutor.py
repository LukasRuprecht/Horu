"""What Horu asks the AI: translations, word explanations, and line breakdowns.

Each task starts a fresh chat whose first message carries the game line and its context;
follow-up questions continue the same chat.
"""

from __future__ import annotations

import dataclasses
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

The game text was captured with OCR and may contain misread characters. If the current \
line looks misread, make the very first line of your reply "OCR: " followed by the whole \
line as it most likely reads (plain text, no readings in brackets), then explain that \
version. Only do this for a likely OCR \
error, never for dialect, slang, baby talk, or unusual but valid Japanese."""

TRANSLATE = """\
{context}Translate this line into natural English that fits the scene:
{line}

Reply with only the English translation of what is said, without the speaker's name: no \
quotes, Japanese, notes, explanations, or romaji."""

# With the line's screenshot: the translation and the check of OCR's reading in one request.
# The "OCR: " line is the same one explanations use, so it's handled the same way.
TRANSLATE_CHECKED = """\
{context}The image shows this line in the game. OCR read it as:
{line}

First check OCR's reading against the image. If OCR misread anything, make the very first \
line of your reply "OCR: " followed by the whole line as the image shows it (the Japanese \
only, without the speaker or readings). Then translate the line, as the image shows it, into \
natural English that fits the scene. If OCR read it correctly, start straight with the \
translation.

Reply with only that: no speaker name, quotes, other Japanese, notes, explanations, or \
romaji."""

# Several lines that appeared together (a choice under a line, a menu of replies): one
# request, one numbered reply line each.
TRANSLATE_LINES = """\
{context}These lines are on screen together:
{lines}

Translate each into natural English that fits the scene. Reply with one line per number, \
like "1. <translation>", and nothing else: no speaker names, quotes, Japanese, notes, or \
romaji."""

# Text without a speaker may be narration, but is often help, menu, or tutorial text, which
# shouldn't be rewritten as speech.
TRANSLATE_TEXT = """\
{context}Translate this text from a video game into natural English. It may be narration, or help, \
menu, or tutorial text; keep its tone, and keep any line breaks and lists.

{text}

Reply with only the English translation: no quotes, Japanese, notes, explanations, or romaji."""

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

# The OCR models that disagreed with the main reading. The true text is usually one of these
# or close to them, so a model can name the likely misread instead of inventing a word.
OCR_ALTERNATIVES = """\
OCR note: other OCR models read the current line differently:
{readings}
The real text is most likely one of these readings or close to them.

"""

REREAD = """\
This image is a line of text from a video game. OCR read it as:
「{text}」
{alternatives}Transcribe the Japanese text in the image exactly, character for character. Pay \
attention to small kana (っ ゃ ゅ ょ ぁ ぃ ぅ ぇ ぉ) and to look-alike characters. Leave out \
small furigana readings printed above kanji. Join the lines into one, without line breaks \
or added spaces. Reply with only the text."""

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
    # Other OCR models' readings of this line, where they disagreed.
    alternatives: list[str] = field(default_factory=list)

    def quoted(self) -> str:
        return f"{self.speaker}「{self.text}」" if self.speaker else f"「{self.text}」"

    def context(self) -> str:
        parts = []
        if self.previous:
            parts.append("Earlier lines, for context:\n" + "\n".join(self.previous) + "\n\n")
        if self.alternatives:
            readings = "\n".join(f"「{a}」" for a in self.alternatives)
            parts.append(OCR_ALTERNATIVES.format(readings=readings))
        return "".join(parts)


# Effort for quick tasks (translations, transcriptions, names): no thinking first, where the
# model allows. On DeepSeek's flash model this halves the wait and cuts output tokens tenfold,
# with translations as good.
QUICK = "none"


class Tutor:
    def __init__(self, provider: Provider, level: str, translator: Provider | None = None) -> None:
        self.provider = provider
        self.level = level
        self.translator = translator

    def _chat(self, effort: str = "low", temperature: float | None = None) -> Chat:
        return self.provider.chat(SYSTEM.format(level=self.level), effort=effort, temperature=temperature)

    def translate(self, line: Line, png: bytes | None = None) -> Iterator[str]:
        """The line's translation. With its screenshot `png`, the reply starts with an "OCR: "
        line when the image shows different text. The image needs a model that reads images
        (a separate translation model gets none)."""
        # Temperature 0 where supported: the same line should get the same translation.
        if self.translator:
            return self.translator.chat("", QUICK, history=TRANSLATE_ONLY_SETUP, temperature=0).send(line.text)
        if png is not None:
            return self._chat(QUICK, 0).send(TRANSLATE_CHECKED.format(context=line.context(), line=line.quoted()), png)
        if line.speaker is None and not line.previous:
            return self._chat(QUICK, 0).send(TRANSLATE_TEXT.format(context=line.context(), text=line.text))
        return self._chat(QUICK, 0).send(TRANSLATE.format(context=line.context(), line=line.quoted()))

    def translate_lines(self, lines: list[Line]) -> Iterator[str] | None:
        """Translations of lines shown together, as numbered reply lines (see
        numbered_lines). None with a separate translation model, which translates one line
        at a time. Context comes from the first line."""
        if self.translator:
            return None
        numbered = "\n".join(f"{i}. {line.quoted()}" for i, line in enumerate(lines, 1))
        prompt = TRANSLATE_LINES.format(context=dataclasses.replace(lines[0], alternatives=[]).context(), lines=numbered)
        return self._chat(QUICK, 0).send(prompt)

    def translate_speaker(self, text: str) -> str:
        """Short English for a speaker label, e.g. 謎の通信 -> "Mysterious transmission"."""
        if self.translator:
            chunks = self.translator.chat("", QUICK, history=TRANSLATE_ONLY_SETUP, temperature=0).send(text)
        else:
            chunks = self.provider.chat("", QUICK, temperature=0).send(SPEAKER.format(text=text))
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

    def reread(self, line: Line, png: bytes) -> str:
        """The line's text as a vision model reads it from the screenshot."""
        alternatives = ""
        if line.alternatives:
            alternatives = "Other OCR models read it as: " + " / ".join(f"「{a}」" for a in line.alternatives) + "\n"
        prompt = REREAD.format(text=line.text, alternatives=alternatives)
        return clean_reading(self.provider.read_image(prompt, png, QUICK))

    def explain_line(self, line: Line) -> tuple[Chat, Iterator[str]]:
        chat = self._chat()
        return chat, chat.send(EXPLAIN_LINE.format(context=line.context(), line=line.quoted()))


_JAPANESE_CHAR = "　-ヿ㐀-鿿！-｠…"
_SPACE_BY_JAPANESE = re.compile(f"(?<=[{_JAPANESE_CHAR}])[ 　]+|[ 　]+(?=[{_JAPANESE_CHAR}])")


_NUMBERED = re.compile(r"^\s*(\d+)\s*[.)．：:]\s*(.*\S)\s*$")


def numbered_lines(reply: str) -> dict[int, str]:
    """{number: translation} from a reply to TRANSLATE_LINES, ignoring anything else."""
    found = {}
    for text in reply.splitlines():
        match = _NUMBERED.match(text)
        if match and not match.group(2).startswith("OCR:"):
            found.setdefault(int(match.group(1)), match.group(2).strip("\"「」"))
    return found


def clean_reading(reply: str) -> str:
    """A model's transcription, without quotes, labels, or line breaks around it."""
    lines = [l.strip() for l in reply.strip().splitlines() if l.strip()]
    text = "".join(lines)
    for prefix in ("Text:", "Transcription:"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    # Models space out words (じゃが　まだまだ) as the game did; Horu's lines have no spaces
    # between Japanese text.
    text = _SPACE_BY_JAPANESE.sub("", text)
    return text.strip("「」『』\"'`")


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
