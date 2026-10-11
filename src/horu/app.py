"""The Horu desktop app: a pywebview window that shows captured lines word by word."""

from __future__ import annotations

import base64
import dataclasses
import io
import itertools
import json
import os
import queue
import sys
import threading
import time
import traceback
import unicodedata
import webbrowser
from collections import OrderedDict, deque
from pathlib import Path

from horu import ai_setup, anki, autocapture, capture, config, correction, corrections, dictionary, hotkey, layout, llm, prefs, speakers, text_sources
from horu.nlp import analyze
from horu.text_sources import rapidocr_source
from horu.text_sources.vote import edit_distance
from horu.tutor import Line, Tutor

UI_DIR = Path(__file__).parent / "ui"
# Blocks already shown recently are skipped, so persistent text (HUD, menus) and repeated
# hotkey presses on the same dialogue don't flood the history.
_RECENT_LINES = 20
# A line read again is still a repeat when OCR reads it a little differently (１ for 1, 喜 for
# 暮): up to one character in this many may differ. Short lines must match exactly.
_REPEAT_SLACK = 10
# Earlier dialogue lines sent along with AI requests, so pronouns and names make sense.
_CONTEXT_LINES = 4
# Lines OCR read with less confidence than this (even after voting) are marked as possibly
# misread, so nobody studies a wrong character without knowing.
_UNCERTAIN = 0.9
# Screenshots of recent lines, kept for flashcards.
_SCREENSHOTS_KEPT = 300
_SCREENSHOT_MAX_WIDTH = 900
# Streamed AI text is pushed to the page at most this often.
_FLUSH_SECONDS = 0.05
# How often auto-capture looks at the dialogue box.
_WATCH_SECONDS = 0.25
# Hotkey captures whose game picture is kept, for drawing auto-read regions on.
_PICTURES_KEPT = 10
_PICTURE_MAX_WIDTH = 1280


@dataclasses.dataclass
class AutoRead:
    """A capture asked for by auto-capture: the game picture, already grabbed."""

    image: object  # PIL image of the game window, as the hotkey would capture it
    game: str
    region: str = "picture"  # where it was watched, for joining a line read while still typing


class Api:
    """Methods JavaScript can call as `window.pywebview.api.<name>(...)`."""

    def __init__(self, app: App) -> None:
        self._app = app

    def lookup(self, word: dict) -> dict:
        return self._app.lookup(word)

    def update_line(self, line_id: int, text: str) -> list[dict]:
        """Replace a line's text (fixing an OCR mistake) and return its new words."""
        return self._app.update_line(line_id, text)

    def ai_info(self) -> dict:
        return self._app.ai_info()

    def translate(self, line_id: int) -> None:
        self._app.translate(line_id)

    # The page picks the session id, so it can accept the first streamed chunk even if
    # it arrives before this call returns.
    def explain_word(self, line_id: int, index: int, session: int, part: str = "text") -> None:
        self._app.explain_word(line_id, index, session, part)

    def explain_line(self, line_id: int, session: int) -> None:
        self._app.explain_line(line_id, session)

    def ask(self, session: int, question: str) -> None:
        self._app.ask(session, question)

    # AI setup (settings panel)

    def ai_settings(self) -> dict:
        return self._app.ai_settings()

    def set_ai(self, values: dict) -> dict:
        return self._app.set_ai(values)

    def save_api_key(self, preset: str, key: str) -> dict:
        ai_setup.save_key(preset, key.strip())
        return self._app.apply_ai()

    def ollama_models(self) -> dict:
        return ai_setup.ollama_models()

    def ollama_pull(self, name: str) -> None:
        self._app.ollama_pull(name)

    def test_ai(self) -> dict:
        return self._app.test_ai()

    def reread(self, line_id: int) -> dict:
        """Read a line again from its screenshot with a vision model."""
        return self._app.reread(line_id)

    def set_speaker(self, line_id: int, text: str) -> None:
        """Replace a line's speaker name (undoing a correction)."""
        self._app.set_speaker(line_id, text)

    def region_editor(self, line_id: int) -> dict:
        """The game picture this line was captured from, with its auto-read regions."""
        return self._app.region_editor(line_id)

    def save_regions(self, line_id: int, regions: list[dict]) -> dict:
        """Replace the auto-read regions for the game and layout of this line's capture."""
        return self._app.save_regions(line_id, regions)

    def open_url(self, url: str) -> None:
        # Links open in the user's browser; the app window itself never navigates away.
        if url.startswith(("https://", "http://")):
            webbrowser.open(url)

    def get_prefs(self) -> dict:
        return self._app.prefs.values

    def set_pref(self, key: str, value) -> None:
        self._app.prefs.set(key, value)
        if key == "onTop":
            self._app.window.on_top = bool(value)
        elif key == "hideFromCapture":
            self._app.apply_capture_exclusion()

    def mark(self, line_id: int, label: str, part: str = "text") -> dict:
        """Remember a correction for this line's text (or its speaker) in this game."""
        return self._app.mark(line_id, label, part)

    def set_speaker_english(self, game: str, text: str, english: str) -> None:
        """The user's own English for a speaker; replaces any cached translation."""
        self._app.set_speaker_english(game, text, english)

    def translate_speaker(self, game: str, text: str) -> None:
        self._app.translate_speaker(game, text)

    def anki_status(self) -> dict:
        return {**self._app.anki.status(), "deck": self._app.prefs.values["ankiDeck"]}

    def save_card(self, line_id: int, index: int, part: str = "text") -> dict:
        return self._app.save_card(line_id, index, part)

    def make_cards(self, line_id: int, session: int) -> None:
        self._app.make_cards(line_id, session)

    def clear(self) -> None:
        self._app.recent.clear()
        self._app.lines.clear()


class App:
    def __init__(self, cfg: config.Config, debug: bool = False) -> None:
        self.cfg = cfg
        self.debug = debug
        self.window = None
        self.prefs = prefs.Prefs()
        self.corrections = corrections.Corrections()
        self.speakers = speakers.SpeakerBook()
        self.boxes = autocapture.DialogueBoxes()
        self._find_text = None  # text detector for auto-capture, loaded when first needed
        self._pictures = autocapture.PictureFinder()
        self._game_window: tuple[int, str] | None = None  # (window, game) of the last hotkey capture
        self._auto_state: tuple[str, str] | None = None  # last state shown on the auto toggle
        self._last_auto: dict[str, tuple[int, str]] = {}  # region -> (line id, text) of the last line read there
        self._captured_pictures: OrderedDict[int, tuple[bytes, tuple[int, int], str]] = OrderedDict()  # capture id -> (JPEG, picture size, game)
        self._started: dict[int, float] = {}  # line id -> when its capture began, for timing logs
        self.anki = anki.AnkiClient()
        self._card_queue: queue.Queue[int] = queue.Queue()  # lines waiting for automatic cards
        self._card_worker: threading.Thread | None = None
        self._screenshots: OrderedDict[int, bytes] = OrderedDict()  # line id -> PNG
        self._speaker_shots: dict[int, bytes] = {}  # line id -> PNG of a doubtful name tag, until checked
        self._speakers_pending: set[tuple[str, str]] = set()  # AI speaker translations in flight
        self.recent: deque[tuple[str, int]] = deque(maxlen=_RECENT_LINES)  # (text, line id) shown recently
        self.lines: dict[int, dict] = {}  # every line shown, in order, by id
        self._ai = ai_setup.Resolved()
        self._vision: bool | None = None  # whether the AI model reads images (None: not known yet)
        self._tutor_chat: tuple[int, llm.Chat] | None = None  # (session id, chat) in the tutor panel
        self._source: text_sources.TextSource | None = None
        self._dictionary: dictionary.Dictionary | None = None
        self._requests: queue.Queue[AutoRead | None] = queue.Queue()  # None: the hotkey
        self._ids = itertools.count(1)
        self._capture_ids = itertools.count(1)
        self._page_loaded = threading.Event()
        self._stop_hotkey = None

    def run(self) -> None:
        import webview

        self.window = webview.create_window(
            "Horu",
            url=str(UI_DIR / "index.html"),
            js_api=Api(self),
            width=560,
            height=900,
            min_size=(360, 400),
            on_top=self.prefs.values["onTop"],
            background_color="#15171d",
        )
        self.window.events.loaded += self._page_loaded.set
        _quit_on_ctrl_c()
        # webview.start blocks on the main thread; _worker runs on a background thread.
        webview.start(self._worker, debug=self.debug)
        if self._stop_hotkey:
            self._stop_hotkey()

    def lookup(self, word: dict) -> dict:
        """Dictionary entries for a word, plus name readings for names (or when the dictionary
        has nothing, since unusual names are often tokenized as ordinary nouns)."""
        if not self._dictionary:
            return {"entries": [], "names": []}
        entries = self._dictionary.lookup(word)
        names = self._dictionary.lookup_names(word) if word["pos"] == "name" or not entries else []
        return {"entries": entries, "names": names}

    def apply_capture_exclusion(self) -> None:
        hwnd = self.window.native.Handle.ToInt32()
        capture.exclude_from_capture(hwnd, self.prefs.values["hideFromCapture"])

    def mark(self, line_id: int, label: str, part: str = "text") -> dict:
        line = self.lines.get(line_id)
        if line is None:
            return {}
        text = line["speaker"]["text"] if part == "speaker" and line["speaker"] else line["text"]
        self.corrections.mark(line["game"], text, label)
        if part == "text" and label in ("dialogue", "other"):
            line["kind"] = label
        return {"kind": line["kind"], "game": line["game"]}

    # --- Anki ----------------------------------------------------------------------------

    def card_for(self, line_id: int, index: int, part: str = "text") -> anki.Card:
        """A flashcard for one word of a line, filled in from Horu's own analysis."""
        line = self.lines[line_id]
        word = (line["speaker"]["words"] if part == "speaker" else line["words"])[index]
        found = self.lookup(word)
        if found["entries"]:
            senses = found["entries"][0]["senses"][:2]
            meaning = "; ".join(", ".join(s["gloss"][:3]) for s in senses)
        elif found["names"]:
            meaning = ", ".join(found["names"][0]["names"][0]["det"])
        else:
            meaning = ""
        return anki.Card(
            sentence=line["text"],
            word=word["surface"],
            dictionary_form=word["dictionary_form"],
            reading=word["dictionary_reading"],
            meaning=meaning,
            notes=", ".join(word["grammar"]),
            translation=line.get("translation", ""),
            source=" · ".join(filter(None, [line["speaker"] and line["speaker"]["text"], line["game"].removesuffix(".exe"), time.strftime("%Y-%m-%d")])),
        )

    def save_card(self, line_id: int, index: int, part: str = "text") -> dict:
        """Add one word as a card. With an AI provider, its meaning and note are written for
        this line; otherwise the card uses the dictionary meaning."""
        if line_id not in self.lines:
            return {"ok": False, "message": "That line is no longer available."}
        card = self.card_for(line_id, index, part)
        deck = self.prefs.values["ankiDeck"] or "Horu"
        if self._provider:
            line = self.lines[line_id]
            word = (line["speaker"]["words"] if part == "speaker" else line["words"])[index]
            try:
                meaning, note = self._tutor().card_note(self._line_context(line_id), word)
            except llm.AIError:
                meaning, note = "", ""  # the dictionary meaning is still a good card
            card.meaning = meaning or card.meaning
            card.notes = "; ".join(filter(None, [note, card.notes]))
        try:
            self.anki.add_card(deck, card, self._screenshots.get(line_id))
        except anki.AnkiError as e:
            return {"ok": False, "message": str(e)}
        return {"ok": True, "message": f"Added {card.dictionary_form} to {deck}."}

    def reread(self, line_id: int) -> dict:
        """{"ok", "text"} with a vision model's reading of the line, or {"ok": False, "message"}."""
        if not self._provider:
            return {"ok": False, "message": "Set up an AI provider in Settings to re-read lines."}
        png = self._screenshots.get(line_id)
        if line_id not in self.lines or png is None:
            return {"ok": False, "message": "The screenshot for this line is no longer kept."}
        try:
            text = self._tutor().reread(self._line_context(line_id), png)
        except llm.AIError as e:
            return {"ok": False, "message": str(e)}
        if not text:
            return {"ok": False, "message": "The AI didn't return any text."}
        return {"ok": True, "text": text}

    def make_cards(self, line_id: int, session: int) -> None:
        """Let the AI pick words from a line and add them to Anki itself, using tools. Its
        steps and summary appear in the tutor panel."""
        if not self._provider or line_id not in self.lines:
            return
        meta = {"target": "tutor", "session": session}
        if self._tutor_chat:
            self._tutor_chat[1].cancel()
            self._tutor_chat = None

        def run() -> None:
            from horu import agent

            try:
                result = agent.make_cards(self, line_id, lambda step: self._call("agentStep", {**meta, "text": step}))
                if result.added:
                    self._call("cardsAdded", {"lineId": line_id, "words": result.added})
                self._call("aiChunk", {**meta, "text": result.summary or "Done."})
                self._call("aiDone", meta)
            except llm.AIError as e:
                self._call("aiError", {**meta, "message": str(e)})
            except Exception as e:
                traceback.print_exc()
                self._call("aiError", {**meta, "message": f"Something went wrong: {e}"})

        threading.Thread(target=run, name="horu-agent", daemon=True).start()

    def queue_auto_cards(self, line_ids: list[int]) -> None:
        """Make cards for these lines in the background, one line at a time."""
        for line_id in line_ids:
            self._card_queue.put(line_id)
        if self._card_worker is None or not self._card_worker.is_alive():
            self._card_worker = threading.Thread(target=self._auto_cards, name="horu-auto-cards", daemon=True)
            self._card_worker.start()

    def _auto_cards(self) -> None:
        from horu import agent

        while True:
            try:
                line_id = self._card_queue.get(timeout=1)
            except queue.Empty:
                return  # restarted by the next capture
            if not self._provider or line_id not in self.lines or not self.prefs.values["autoCards"]:
                continue
            short = self.lines[line_id]["text"][:12]
            try:
                result = agent.make_cards(
                    self, line_id, lambda step: self._status("busy", f"Cards for 「{short}…」: {step}...")
                )
            except llm.AIError as e:
                self._status("error", f"Automatic cards: {e}")
                continue
            except Exception as e:
                traceback.print_exc()
                self._status("error", f"Automatic cards failed: {e}")
                continue
            if result.added:
                self._call("cardsAdded", {"lineId": line_id, "words": result.added})
                self._ready(f"Added {len(result.added)} card(s): {', '.join(result.added)}.")
            else:
                self._ready()

    def _keep_screenshot(self, line_id: int, image, box: tuple[int, int, int, int]) -> None:
        """A crop around the line (with some surrounding scene) for its flashcards."""
        self._screenshots[line_id] = _png(image, box, margin=max(40, box[3] - box[1]))
        while len(self._screenshots) > _SCREENSHOTS_KEPT:
            self._screenshots.popitem(last=False)

    def _words(self, text: str) -> list[dict]:
        """The words of a line, for the UI. With the dictionary loaded, words come out as it has
        them: pieces the tokenizer split apart (か + かわり, ねー + ちゃん) are joined, and
        phrases it doesn't know (太陽の歌) are shown word by word."""
        if not self._dictionary:
            return [w.to_dict() for w in analyze(text)]
        return [w.to_dict() for w in analyze(text, self._dictionary.has_form, self._dictionary.kinds)]

    def update_line(self, line_id: int, text: str) -> list[dict]:
        words = self._words(text)
        if line_id in self.lines:
            self.lines[line_id].update(text=text, words=words)
        return words

    # --- AI ----------------------------------------------------------------------------

    @property
    def _provider(self) -> llm.Provider | None:
        return self._ai.provider

    def ai_info(self) -> dict:
        ai = self._ai
        return {
            "enabled": ai.provider is not None,
            "label": ai.provider.label if ai.provider else None,
            "translator": ai.translator.label if ai.translator else None,
            "error": ai.error,
            "source": ai.source,
        }

    def apply_ai(self) -> dict:
        """Rebuild the providers from the current settings and tell the page."""
        self._ai = ai_setup.resolve(self.prefs.values, self.cfg.ai)
        self._vision = None
        info = self.ai_info()
        self._call("setAI", info)
        return info

    def ai_settings(self) -> dict:
        values = self.prefs.values
        if not values["aiProvider"]:  # nothing chosen in the app yet: show config.toml's setup
            values = {**values, **ai_setup.settings_from_config(self.cfg.ai)}
        return {
            "provider": values["aiProvider"],
            "model": values["aiModel"],
            "baseUrl": values["aiBaseUrl"],
            "translationModel": values["translationModel"],
            "presets": ai_setup.PRESETS,
            "savedKeys": [p for p in ai_setup.PRESETS if ai_setup.has_saved_key(p)],
            "ollamaSuggestions": ai_setup.OLLAMA_SUGGESTIONS,
        }

    def set_ai(self, values: dict) -> dict:
        allowed = {"aiProvider", "aiModel", "aiBaseUrl", "translationModel"}
        self.prefs.update({k: v for k, v in values.items() if k in allowed})
        return self.apply_ai()

    def test_ai(self) -> dict:
        ai = self._ai
        if ai.provider is None:
            return {"ok": False, "message": ai.error or "AI is off."}
        results = []
        for role, provider in (("Explanations", ai.provider), ("Translations", ai.translator)):
            if provider is None:
                continue
            start = time.perf_counter()
            try:
                reply = ai_setup.test(provider)
            except llm.AIError as e:
                return {"ok": False, "message": f"{role}: {e}"}
            results.append(f"{role}: replied \"{reply}\" in {time.perf_counter() - start:.1f}s")
        return {"ok": True, "message": " · ".join(results)}

    def ollama_pull(self, name: str) -> None:
        def run() -> None:
            last = 0.0
            try:
                def progress(event: dict) -> None:
                    nonlocal last
                    if time.monotonic() - last >= 0.25:  # throttle page updates
                        last = time.monotonic()
                        self._call("pullProgress", {"name": name, **event})

                ai_setup.ollama_pull(name, progress)
                self._call("pullProgress", {"name": name, "status": "success", "done": True})
            except llm.AIError as e:
                self._call("pullProgress", {"name": name, "status": "error", "error": str(e), "done": True})

        threading.Thread(target=run, name="horu-ollama-pull", daemon=True).start()

    # --- speaker names -------------------------------------------------------------------

    def speaker_english(self, game: str, text: str) -> str | None:
        """Cached English for a speaker, filling the cache from the names dictionary when the
        speaker is a person's name (free and exact, e.g. 順平 -> Junpei)."""
        cached = self.speakers.english(game, text)
        if cached is None and not layout.is_japanese(text):
            # ？？？ and the like need no translation.
            cached = text.replace("？", "?").replace("！", "!")
            self.speakers.set_english(game, text, cached, "as-is")
        if cached is None and (romaji := self._name_romaji(text)):
            self.speakers.set_english(game, text, romaji, "names")
            cached = romaji
        return cached

    def translate_speaker(self, game: str, text: str) -> None:
        """Ask the AI for a speaker's English once; the page is told when it arrives."""
        if not self._provider or self.speaker_english(game, text) or (game, text) in self._speakers_pending:
            return
        self._speakers_pending.add((game, text))

        def run() -> None:
            try:
                english = self._tutor().translate_speaker(text)
                if english:
                    self.speakers.set_english(game, text, english, "ai")
                    self._call("speakerEnglish", {"game": game, "text": text, "english": english})
            except llm.AIError:
                pass  # the name is still shown; a failed label translation isn't worth an alert
            except Exception:
                traceback.print_exc()
            finally:
                self._speakers_pending.discard((game, text))

        threading.Thread(target=run, name="horu-speaker", daemon=True).start()

    def set_speaker_english(self, game: str, text: str, english: str) -> None:
        self.speakers.set_english(game, text, english.strip(), "user")
        self._call("speakerEnglish", {"game": game, "text": text, "english": english.strip()})

    def _name_romaji(self, text: str) -> str | None:
        """"Junpei" for 順平, "Sakamoto Ryūji" for 坂本竜司: only when every word is a person's
        name with a matching reading."""
        if not self._dictionary:
            return None
        words = [w for w in analyze(text) if w.pos != "symbol"]
        if not words or any(w.pos != "name" for w in words):
            return None
        parts = []
        for word in words:
            names = self._dictionary.lookup_names(word.to_dict())
            person = next(
                (
                    n
                    for n in names
                    if word.reading in n["readings"] and any(set(t["types"]) & _PERSON_NAME_TYPES for t in n["names"])
                ),
                None,
            )
            if person is None:
                return None
            parts.append(person["names"][0]["det"][0])
        return " ".join(parts)

    def translate(self, line_id: int) -> None:
        if self._provider and line_id in self.lines:
            chunks = self._tutor().translate(self._line_context(line_id))
            # Numbered, so the page and the stored translation keep only the latest one when
            # a fixed line is translated again while the first translation still streams.
            generation = self.lines[line_id]["translationGen"] = self.lines[line_id].get("translationGen", 0) + 1
            self._stream({"target": "translation", "lineId": line_id, "gen": generation}, chunks)
            line = self.lines[line_id]
            if line["speaker"]:
                self.translate_speaker(line["game"], line["speaker"]["text"])

    def explain_word(self, line_id: int, index: int, session: int, part: str = "text") -> None:
        if self._provider and line_id in self.lines:
            line = self.lines[line_id]
            word = (line["speaker"]["words"] if part == "speaker" else line["words"])[index]
            chat, chunks = self._tutor().explain_word(self._line_context(line_id), word)
            self._start_tutor_session(session, chat, chunks, line_id)

    def explain_line(self, line_id: int, session: int) -> None:
        if self._provider and line_id in self.lines:
            chat, chunks = self._tutor().explain_line(self._line_context(line_id))
            self._start_tutor_session(session, chat, chunks, line_id)

    def ask(self, session: int, question: str) -> None:
        if self._tutor_chat and self._tutor_chat[0] == session:
            self._stream({"target": "tutor", "session": session}, self._tutor_chat[1].send(question))

    def _tutor(self) -> Tutor:
        return Tutor(self._provider, self.prefs.values["learnerLevel"], self._ai.translator)

    def _start_tutor_session(self, session: int, chat: llm.Chat, chunks, line_id: int) -> None:
        # The tutor panel shows one conversation at a time; stop the previous one.
        if self._tutor_chat:
            self._tutor_chat[1].cancel()
        self._tutor_chat = (session, chat)
        self._stream({"target": "tutor", "session": session, "lineId": line_id}, chunks)

    def _line_context(self, line_id: int) -> Line:
        line = self.lines[line_id]
        # Earlier dialogue only helps actual dialogue: help, menu, and tutorial text (no
        # speaker) is translated on its own.
        previous = []
        if line["speaker"]:
            earlier = [l for i, l in self.lines.items() if i < line_id and l["speaker"] and not l["repeat"]]
            previous = [Line(l["text"], _speaker_text(l)).quoted() for l in earlier[-_CONTEXT_LINES:]]
        return Line(line["text"], _speaker_text(line), previous, line.get("alternatives", []))

    def _stream(self, meta: dict, chunks) -> None:
        """Push an AI reply to the page as it arrives, on a background thread."""

        def run() -> None:
            buffer: list[str] = []
            full: list[str] = []
            last_flush = time.monotonic()
            try:
                for chunk in chunks:
                    if not full and meta.get("target") == "translation":
                        self._time(meta["lineId"], "translation started")
                    buffer.append(chunk)
                    full.append(chunk)
                    if time.monotonic() - last_flush >= _FLUSH_SECONDS:
                        self._call("aiChunk", {**meta, "text": "".join(buffer)})
                        buffer.clear()
                        last_flush = time.monotonic()
                if buffer:
                    self._call("aiChunk", {**meta, "text": "".join(buffer)})
                reply = "".join(full).strip()
                # A reply about a line can start with the model's fix for a misread
                # ("OCR: ..."); the page hides that line.
                proposal = correction.proposed_in(reply) if "lineId" in meta else None
                if meta.get("target") == "translation" and meta["lineId"] in self.lines:
                    line = self.lines[meta["lineId"]]
                    if meta["gen"] == line.get("translationGen"):
                        line["translation"] = correction.without_proposal(reply)
                self._call("aiDone", meta)
                if meta.get("target") == "translation":
                    self._time(meta["lineId"], "translated")
                if proposal:
                    self.propose(meta["lineId"], proposal, "explain")
            except llm.AIError as e:
                self._call("aiError", {**meta, "message": str(e)})
            except Exception as e:
                traceback.print_exc()
                self._call("aiError", {**meta, "message": f"Something went wrong: {e}"})

        threading.Thread(target=run, name="horu-ai", daemon=True).start()

    # --- background work -------------------------------------------------------------

    def _worker(self) -> None:
        self._page_loaded.wait()
        self.apply_capture_exclusion()
        if not self._load():
            return
        threading.Thread(target=self._watch, name="horu-watch", daemon=True).start()
        while True:
            request = self._requests.get()
            try:
                self._capture(request)
            except Exception as e:  # keep the app alive; show what went wrong
                traceback.print_exc()
                self._status("error", f"Capture failed: {e}")

    def _load(self) -> bool:
        try:
            try:
                self._dictionary = dictionary.Dictionary()
            except dictionary.DictionaryMissing:
                self._status("loading", "Downloading the dictionary (first run only)...")
                dictionary.build()
                self._dictionary = dictionary.Dictionary()
            if not self._dictionary.has_names:  # dictionaries built before names were added
                self._status("loading", "Downloading the names dictionary (one time only)...")
                dictionary.build_names()
                self._dictionary = dictionary.Dictionary()
            self._status("loading", f"Loading OCR ({self.cfg.ocr_engine})...")
            self._source = text_sources.create(self.cfg.ocr_engine)
            self._stop_hotkey = hotkey.listen(self.cfg.capture_hotkey, lambda: self._requests.put(None))
            self.apply_ai()  # AI is optional: problems are reported in settings, not fatal
        except Exception as e:
            traceback.print_exc()
            self._status("error", str(e))
            return False
        self._ready()
        return True

    def _capture(self, request: AutoRead | None = None) -> None:
        started = time.monotonic()
        auto = request is not None
        if auto:
            image, game = request.image, request.game
        else:
            if self.cfg.capture.mode == "window" and capture.focused_window_is_own():
                self._status("error", "Horu has focus. Click your game, then press the hotkey.")
                return
            self._status("busy", "Reading...")
            image = capture.grab(self.cfg.capture)
            game = (capture.focused_process_name() if self.cfg.capture.mode == "window" else None) or corrections.DEFAULT_GAME
            window = capture.foreground_window() if self.cfg.capture.mode == "window" else None
            if window:
                self._game_window = (window, game)
        if self.cfg.save_captures:
            capture.save(image, self.cfg.captures_dir)
        hints = dataclasses.replace(self.corrections.hints(game), known_speakers=self.speakers.known(game))
        blocks = layout.group_blocks(self._source.read(image), image, hints=hints)
        # Correct misread name tags against names already seen in this game.
        blocks = [
            dataclasses.replace(b, speaker=self.speakers.resolve(game, b.speaker, b.speaker_score)) if b.speaker else b
            for b in blocks
        ]

        picture = autocapture.picture_box(image) if not auto else None
        if auto:
            # Only dialogue: a sign the camera stopped on, a location name, or a menu would be
            # read some of the time. The hotkey reads those.
            blocks = [b for b in blocks if b.kind == "dialogue"]

        # Text shown recently stays in the capture but is marked as a repeat, so the page can
        # collapse it instead of lines silently going missing (e.g. when scrolling a backlog).
        capture_id = next(self._capture_ids)
        lines = []
        for block in blocks:
            earlier = next((i for text, i in self.recent if _same_line(block.text, text)), None)
            line = self._line(block, earlier is not None, game)
            line["game"] = game
            if earlier is None:
                self.recent.append((block.text, line["id"]))
            elif self.lines.get(earlier, {}).get("translation"):
                line["translation"] = self.lines[earlier]["translation"]  # shown with it, no new request
            if not auto:  # where it was, for drawing auto-read regions
                box, size = autocapture.in_picture(block.box, picture)
                line["capture"], line["box"], line["frame"], line["rows"] = capture_id, list(box), list(size), len(block.lines)
            if line["speaker"]:
                line["speaker"]["english"] = self.speaker_english(game, block.speaker)
            lines.append(line)
        new = [l for l in lines if not l["repeat"]]
        if auto and not new:
            return  # an empty box, or text already shown: nothing to say
        if not lines:
            self._ready("No Japanese text found.")
            return
        if not new:
            self._ready(f"Nothing new: all {len(lines)} line(s) were already shown.")
            return
        if auto and self._extends_last(request.region, new, blocks, lines, image):
            return
        if not auto:
            self._keep_picture(capture_id, image, picture, game)
        for line, block in zip(lines, blocks):
            self.lines[line["id"]] = line
            if not line["repeat"]:
                self._keep_screenshot(line["id"], image, block.box)
                if line["speaker"] and line["speaker"]["uncertain"]:
                    self._speaker_shots[line["id"]] = _png(image, block.speaker_line.box, margin=8)
        self._call("addCapture", {"id": capture_id, "time": time.strftime("%H:%M:%S"), "lines": lines, "auto": auto})
        for line in new:
            self._started[line["id"]] = started
            self._time(line["id"], "shown" + (" (flagged)" if line["uncertain"] else ""))
        self._ready()
        dialogue_new = [l for l in new if l["kind"] == "dialogue"]
        if auto:
            if dialogue_new:
                self._last_auto[request.region] = (dialogue_new[-1]["id"], dialogue_new[-1]["text"])
            else:
                self._last_auto.pop(request.region, None)
        # A vision model checks every dialogue line against its screenshot, while the line is
        # translated; a line it fixes is translated again. Cards for lines Horu may have
        # misread wait for the check, so they never teach a misreading.
        # Other text (menus, labels) is checked only when flagged: a menu screen has many
        # short items.
        dialogue = [line["id"] for line in new if line["kind"] == "dialogue"]
        flagged_other = [line["id"] for line in new if line["kind"] != "dialogue" and line["uncertain"]]
        checkable = [i for i in dialogue + flagged_other if self._can_check(i)]
        waiting = [i for i in checkable if self.lines[i]["uncertain"] and i in dialogue]
        if self.prefs.values["autoTranslate"]:
            for line_id in dialogue:
                self.translate(line_id)
        self._auto_cards([i for i in dialogue if i not in waiting])
        if checkable:
            threading.Thread(target=self._check_lines, args=(checkable, waiting), name="horu-check", daemon=True).start()

    def _extends_last(self, region: str, new: list[dict], blocks, lines: list[dict], image) -> bool:
        """Text read while it was still typing out (paused at 、 or …) and read again in full:
        the earlier line is updated in place instead of shown twice."""
        if region not in self._last_auto or len(new) != 1:
            return False
        last_id, last_text = self._last_auto[region]
        text = new[0]["text"]
        if last_id not in self.lines or text == last_text or not text.startswith(last_text):
            return False
        line = self.lines[last_id]
        words = self.update_line(last_id, text)
        line.update(uncertain=new[0]["uncertain"], alternatives=new[0]["alternatives"], aiReading=None, suggestion=None)
        self._keep_screenshot(last_id, image, blocks[lines.index(new[0])].box)
        self._last_auto[region] = (last_id, text)
        self._call("lineUpdated", {"id": last_id, "text": text, "words": words, "uncertain": line["uncertain"]})
        # The full line needs its own check (the partial one was checked before).
        if self._can_check(last_id):
            threading.Thread(target=self._check_lines, args=([last_id], []), name="horu-check", daemon=True).start()
        return True

    # --- auto-capture ------------------------------------------------------------------

    def _watch(self) -> None:
        """Look at the game picture a few times a second; read it when new text has settled
        on it."""
        watcher, watching = None, None
        while True:
            time.sleep(_WATCH_SECONDS)
            try:
                frame = self._watch_frame()
                if frame is None:
                    watching = None
                    continue
                key, image, picture, game, regions = frame
                # Another game, window size, or set of regions starts afresh.
                if key != watching:
                    if self._find_text is None:
                        self._find_text = rapidocr_source.text_finder(autocapture.MAP_WIDTH)
                    watcher, watching = autocapture.TextWatcher(self._find_text), key
                if watcher.update(image.crop(picture), regions):
                    # Read like a hotkey capture: the whole window, so it reads the same.
                    self._requests.put(AutoRead(image, game))
            except Exception:
                traceback.print_exc()

    def _watch_frame(self) -> tuple[tuple, object, autocapture.Pixels, str, list[autocapture.Box]] | None:
        """(key, image of the game window, where the game picture is in it, game, the regions to
        limit watching to) when there is something to watch now."""
        if not self.prefs.values["autoCapture"] or self._source is None:
            self._set_auto("off", "Auto-capture is off.")
            return None
        hotkey_name = _pretty_hotkey(self.cfg.capture_hotkey)
        if self._game_window is None:
            self._set_auto("waiting", f"Press {hotkey_name} in your game once, so Horu knows which window to watch.")
            return None
        window, game = self._game_window
        box = capture.window_box(window)
        # Only while the game (or Horu, beside it) is in front: otherwise another window may
        # be covering it.
        foreground = capture.foreground_window()
        if box is None or (foreground != window and not capture.focused_window_is_own()):
            self._set_auto("paused", f"Paused: {game} isn't in front.")
            return None
        size = (box["width"], box["height"])
        picture = self._pictures.find(size, lambda: capture.grab_part(box, (0, 0, *size)))
        picture_size = (picture[2] - picture[0], picture[3] - picture[1])
        regions = self.boxes.regions(game, picture_size)
        # With regions, only they need to be uncovered.
        parts = [autocapture.to_pixels(r.box, picture) for r in regions] or [picture]
        if not all(capture.shows_part(window, box, part, own_ok=self.prefs.values["hideFromCapture"]) for part in parts):
            self._set_auto("paused", f"Paused: something is covering {game}.")
            return None
        if regions:
            self._set_auto("watching", f"Watching {game}: {', '.join(r.name for r in regions)}.")
        else:
            self._set_auto("watching", f"Watching {game} for new dialogue.")
        boxes = [r.box for r in regions]
        return (game, picture, tuple(boxes)), capture.grab_part(box, (0, 0, *size)), picture, game, boxes

    def _time(self, line_id: int, event: str) -> None:
        """Log how long after its capture began a line reached `event`, to find slow steps."""
        if line_id in self._started:
            print(f"[timing] line {line_id}: {event} at {time.monotonic() - self._started[line_id]:.2f}s", flush=True)

    def _set_auto(self, state: str, message: str) -> None:
        if self._auto_state != (state, message):
            self._auto_state = (state, message)
            self._call("setAutoState", {"state": state, "message": message})

    def _keep_picture(self, capture_id: int, image, picture: autocapture.Pixels, game: str) -> None:
        """The game picture of a hotkey capture, for drawing auto-read regions on."""
        crop = image.crop(picture).convert("RGB")
        size = crop.size
        if crop.width > _PICTURE_MAX_WIDTH:
            crop = crop.resize((_PICTURE_MAX_WIDTH, round(crop.height * _PICTURE_MAX_WIDTH / crop.width)))
        buffer = io.BytesIO()
        crop.save(buffer, format="JPEG", quality=85)
        self._captured_pictures[capture_id] = (buffer.getvalue(), size, game)
        while len(self._captured_pictures) > _PICTURES_KEPT:
            self._captured_pictures.popitem(last=False)

    def region_editor(self, line_id: int) -> dict:
        line = self.lines.get(line_id)
        if not line or line.get("capture") not in self._captured_pictures:
            return {"ok": False, "message": "Regions are drawn on one of your last few hotkey captures. Capture a dialogue line and try again."}
        jpeg, size, game = self._captured_pictures[line["capture"]]
        return {
            "ok": True,
            "game": game,
            "image": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii"),
            "aspect": size[0] / size[1],
            "regions": [{"name": r.name, "box": list(r.box)} for r in self.boxes.regions(game, size)],
            "proposed": list(self.boxes.suggestion(tuple(line["box"]), line["rows"], tuple(line["frame"]))),
        }

    def save_regions(self, line_id: int, regions: list[dict]) -> dict:
        line = self.lines.get(line_id)
        if not line or line.get("capture") not in self._captured_pictures:
            return {"ok": False, "message": "That capture is too old to edit regions on. Capture again and retry."}
        _, size, game = self._captured_pictures[line["capture"]]
        cleaned = []
        for i, region in enumerate(regions):
            left, top, right, bottom = (min(max(float(v), 0.0), 1.0) for v in region["box"])
            if right - left > 0.01 and bottom - top > 0.01:
                name = str(region.get("name") or "").strip() or f"Region {i + 1}"
                cleaned.append(autocapture.Region(name, (left, top, right, bottom)))
        self.boxes.set_regions(game, size, cleaned)
        if not cleaned:
            return {"ok": True, "message": f"Auto-capture won't read anything in {game} (in this screen layout)."}
        if not self.prefs.values["autoCapture"]:
            return {"ok": True, "message": f"Saved. Turn on auto-capture (the eye button) to read {game} by itself."}
        return {"ok": True, "message": f"Auto-capture is watching {len(cleaned)} region(s) in {game}."}

    # --- fixing misread lines --------------------------------------------------------

    def _can_check(self, line_id: int) -> bool:
        return bool(self._provider and line_id in self._screenshots and self._vision is not False)

    def _auto_cards(self, line_ids: list[int]) -> None:
        if line_ids and self.prefs.values["autoCards"] and self._provider:
            self.queue_auto_cards(line_ids)

    def _check_lines(self, line_ids: list[int], waiting: list[int]) -> None:
        """Check `line_ids`, flagged lines first; lines fixed are translated again. Cards for
        `waiting` lines are made once they're checked."""
        for line_id in sorted(line_ids, key=lambda i: i not in waiting):
            try:
                self._check(line_id)
            except Exception:
                traceback.print_exc()
            if line_id in waiting:
                self._auto_cards([line_id])

    def _check(self, line_id: int) -> None:
        """Have a vision model read the line (and a doubtful name tag) from its screenshot."""
        self._check_text(line_id)
        if line_id in self._speaker_shots:
            try:
                self._check_speaker(line_id)
            except llm.AIError as e:
                self._time(line_id, f"speaker check failed: {e}")

    def _check_speaker(self, line_id: int) -> None:
        """Read a name tag OCR wasn't sure of again with a vision model, and use that reading
        when it differs. The name it settles on is remembered, so later misreads of it are
        matched to it."""
        png = self._speaker_shots.pop(line_id)
        line = self.lines[line_id]
        speaker, game = line["speaker"], line["game"]
        reading = self._tutor().reread(Line(speaker["text"], alternatives=speaker["alternatives"]), png)
        self._time(line_id, "speaker checked")
        outcome = correction.decide(speaker["text"], reading, speaker["alternatives"])
        if outcome == correction.SAME:
            self.speakers.confirm(game, speaker["text"])
        elif outcome == correction.APPLY or (outcome == correction.SUGGEST and self.prefs.values["applyAiReadings"]):
            self.speakers.confirm(game, reading, was=speaker["text"])
            self.set_speaker(line_id, reading, was=speaker["text"])

    def set_speaker(self, line_id: int, text: str, was: str | None = None) -> None:
        line = self.lines.get(line_id)
        if not line or not line["speaker"]:
            return
        game = line["game"]
        line["speaker"].update(text=text, words=self._words(text), english=self.speaker_english(game, text), was=was)
        self._call("speakerCorrected", {"id": line_id, "speaker": line["speaker"]})
        if not line["speaker"]["english"] and self.prefs.values["autoTranslate"]:
            self.translate_speaker(game, text)

    def _check_text(self, line_id: int) -> None:
        """Have a vision model read the line from its screenshot, and fix the line if that
        reading agrees with another OCR model's."""
        try:
            reading = self._tutor().reread(self._line_context(line_id), self._screenshots[line_id])
            self._time(line_id, "checked")
        except llm.AIError as e:
            self._time(line_id, f"check failed: {e}")
            if "can't read images" in str(e):
                self._vision = False  # a text-only model: stop trying until the AI changes
            return
        self.lines[line_id]["aiReading"] = reading
        self.propose(line_id, reading, "vision")

    def propose(self, line_id: int, text: str, source: str) -> str:
        """Act on a proposed fix for a line: apply it when another source agrees, otherwise
        offer it to the learner. `source` is "vision" or "explain"."""
        line = self.lines.get(line_id)
        if line is None:
            return correction.SAME
        confirmations = list(line.get("alternatives", []))
        if source != "vision" and line.get("aiReading"):
            confirmations.append(line["aiReading"])
        outcome = correction.decide(line["text"], text, confirmations)
        if outcome == correction.SUGGEST and self.prefs.values["applyAiReadings"]:
            outcome = correction.APPLY
        text = correction.keep_spaces(line["text"], text)
        if outcome == correction.SAME and source == "vision" and line.get("uncertain"):
            # The AI read what OCR read: two sources agree, so the line isn't in doubt.
            line["uncertain"] = False
            self._call("lineVerified", {"id": line_id})
        elif outcome == correction.APPLY:
            was = line["text"]
            words = self.update_line(line_id, text)
            line["was"] = line.get("was") or was
            line["suggestion"] = None
            line["uncertain"] = False  # two sources agree on the new text
            self._call("lineCorrected", {"id": line_id, "text": text, "words": words, "was": line["was"]})
        elif outcome == correction.SUGGEST and text != line.get("suggestion"):
            line["suggestion"] = text
            self._call("lineSuggestion", {"id": line_id, "text": text, "source": source})
        return outcome

    def _line(self, block: layout.TextBlock, repeat: bool = False, game: str = "") -> dict:
        speaker = None
        if block.speaker:
            read = block.speaker_line
            speaker = {
                "text": block.speaker,
                "words": self._words(block.speaker),
                # Other OCR models' readings of the name tag, and whether to read it again
                # with the AI: a name it hasn't confirmed yet. Decorated name tags are misread
                # even when OCR is sure (初の男性客 for 初老の男性客, at 0.92).
                "alternatives": list(read.alternatives) if read else [],
                "uncertain": bool(read) and not self.speakers.confirmed(game, block.speaker),
            }
        return {
            "id": next(self._ids),
            "speaker": speaker,
            "repeat": repeat,
            "text": block.text,
            "kind": block.kind,
            # OCR's confidence in its weakest line (speaker included): below this, the line may
            # be misread. Lines the OCR models read differently are flagged too.
            "uncertain": min(block.speaker_score, *(l.score for l in block.lines)) < _UNCERTAIN
            or any(l.alternatives for l in block.lines),
            "alternatives": _block_alternatives(block),
            "words": self._words(block.text),
        }

    # --- talking to the page ---------------------------------------------------------

    def _ready(self, message: str | None = None) -> None:
        self._status("ready", message or f"Ready. Press {_pretty_hotkey(self.cfg.capture_hotkey)} in your game.")

    def _status(self, state: str, message: str) -> None:
        self._call("setStatus", {"state": state, "message": message})

    def _call(self, function: str, *args) -> None:
        payload = ", ".join(json.dumps(a, ensure_ascii=False) for a in args)
        self.window.evaluate_js(f"window.horu && horu.{function}({payload})")


def _same_line(a: str, b: str) -> bool:
    """The same text, allowing for OCR noise (see _REPEAT_SLACK)."""
    a, b = (unicodedata.normalize("NFKC", corrections.normalize(t)) for t in (a, b))
    if a == b:
        return True
    slack = min(len(a), len(b)) // _REPEAT_SLACK
    return slack > 0 and abs(len(a) - len(b)) <= slack and edit_distance(a, b) <= slack


def _png(image, box: tuple[int, int, int, int], margin: int) -> bytes:
    """A PNG of `box` in `image` with `margin` pixels around it, no wider than screenshots are kept."""
    left, top, right, bottom = box
    crop = image.crop((max(left - margin, 0), max(top - margin, 0), min(right + margin, image.width), min(bottom + margin, image.height)))
    if crop.width > _SCREENSHOT_MAX_WIDTH:
        crop = crop.resize((_SCREENSHOT_MAX_WIDTH, round(crop.height * _SCREENSHOT_MAX_WIDTH / crop.width)))
    buffer = io.BytesIO()
    crop.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


_PERSON_NAME_TYPES = {"surname", "given name", "female given name", "male given name", "full name", "character", "fictional"}


def _block_alternatives(block: layout.TextBlock, limit: int = 3) -> list[str]:
    """Other OCR readings of a block's text: the block with one line swapped for another
    model's reading of it. Given to the AI so it can spot a misread line."""
    texts = [l.text for l in block.lines]
    return [
        "".join(texts[:i] + [alt] + texts[i + 1 :]) for i, l in enumerate(block.lines) for alt in l.alternatives
    ][:limit]


def _speaker_text(line: dict) -> str | None:
    return line["speaker"]["text"] if line["speaker"] else None


_console_handler = None  # kept alive: Windows calls it after run() has returned


def _quit_on_ctrl_c() -> None:
    """Quit on Ctrl+C in the terminal. Python's own handler never runs while the window's
    event loop holds the main thread; Windows calls this one on a thread of its own."""
    global _console_handler
    if sys.platform != "win32":
        return
    import ctypes

    @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)
    def handler(event: int) -> int:
        os._exit(0)

    _console_handler = handler
    ctypes.windll.kernel32.SetConsoleCtrlHandler(handler, True)


def _pretty_hotkey(spec: str) -> str:
    return "+".join(part.strip("<>").capitalize() for part in spec.split("+"))
