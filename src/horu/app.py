"""The Horu desktop app: a pywebview window that shows captured lines word by word."""

from __future__ import annotations

import dataclasses
import io
import itertools
import json
import queue
import threading
import time
import traceback
import webbrowser
from collections import OrderedDict, deque
from pathlib import Path

from horu import ai_setup, anki, capture, config, corrections, dictionary, hotkey, layout, llm, prefs, speakers, text_sources
from horu.nlp import analyze
from horu.tutor import Line, Tutor

UI_DIR = Path(__file__).parent / "ui"
# Blocks already shown recently are skipped, so persistent text (HUD, menus) and repeated
# hotkey presses on the same dialogue don't flood the history.
_RECENT_LINES = 20
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
        self.anki = anki.AnkiClient()
        self._card_queue: queue.Queue[int] = queue.Queue()  # lines waiting for automatic cards
        self._card_worker: threading.Thread | None = None
        self._screenshots: OrderedDict[int, bytes] = OrderedDict()  # line id -> PNG
        self._speakers_pending: set[tuple[str, str]] = set()  # AI speaker translations in flight
        self.recent: deque[str] = deque(maxlen=_RECENT_LINES)
        self.lines: dict[int, dict] = {}  # every line shown, in order, by id
        self._ai = ai_setup.Resolved()
        self._tutor_chat: tuple[int, llm.Chat] | None = None  # (session id, chat) in the tutor panel
        self._source: text_sources.TextSource | None = None
        self._dictionary: dictionary.Dictionary | None = None
        self._requests: queue.Queue[None] = queue.Queue()
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
        left, top, right, bottom = box
        margin = max(40, (bottom - top))
        crop = image.crop((max(left - margin, 0), max(top - margin, 0), min(right + margin, image.width), min(bottom + margin, image.height)))
        if crop.width > _SCREENSHOT_MAX_WIDTH:
            crop = crop.resize((_SCREENSHOT_MAX_WIDTH, round(crop.height * _SCREENSHOT_MAX_WIDTH / crop.width)))
        buffer = io.BytesIO()
        crop.save(buffer, format="PNG", optimize=True)
        self._screenshots[line_id] = buffer.getvalue()
        while len(self._screenshots) > _SCREENSHOTS_KEPT:
            self._screenshots.popitem(last=False)

    def _words(self, text: str) -> list[dict]:
        """The words of a line, for the UI. With the dictionary loaded, casual spellings the
        tokenizer splits (ねー + ちゃん) are joined back into words it knows."""
        known = self._dictionary.has_form if self._dictionary else None
        return [w.to_dict() for w in analyze(text, known)]

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
            self._stream({"target": "translation", "lineId": line_id}, chunks)
            line = self.lines[line_id]
            if line["speaker"]:
                self.translate_speaker(line["game"], line["speaker"]["text"])

    def explain_word(self, line_id: int, index: int, session: int, part: str = "text") -> None:
        if self._provider and line_id in self.lines:
            line = self.lines[line_id]
            word = (line["speaker"]["words"] if part == "speaker" else line["words"])[index]
            chat, chunks = self._tutor().explain_word(self._line_context(line_id), word)
            self._start_tutor_session(session, chat, chunks)

    def explain_line(self, line_id: int, session: int) -> None:
        if self._provider and line_id in self.lines:
            chat, chunks = self._tutor().explain_line(self._line_context(line_id))
            self._start_tutor_session(session, chat, chunks)

    def ask(self, session: int, question: str) -> None:
        if self._tutor_chat and self._tutor_chat[0] == session:
            self._stream({"target": "tutor", "session": session}, self._tutor_chat[1].send(question))

    def _tutor(self) -> Tutor:
        return Tutor(self._provider, self.prefs.values["learnerLevel"], self._ai.translator)

    def _start_tutor_session(self, session: int, chat: llm.Chat, chunks) -> None:
        # The tutor panel shows one conversation at a time; stop the previous one.
        if self._tutor_chat:
            self._tutor_chat[1].cancel()
        self._tutor_chat = (session, chat)
        self._stream({"target": "tutor", "session": session}, chunks)

    def _line_context(self, line_id: int) -> Line:
        line = self.lines[line_id]
        # Earlier dialogue only helps actual dialogue: help, menu, and tutorial text (no
        # speaker) is translated on its own.
        previous = []
        if line["speaker"]:
            earlier = [l for i, l in self.lines.items() if i < line_id and l["speaker"] and not l["repeat"]]
            previous = [Line(l["text"], _speaker_text(l)).quoted() for l in earlier[-_CONTEXT_LINES:]]
        return Line(line["text"], _speaker_text(line), previous)

    def _stream(self, meta: dict, chunks) -> None:
        """Push an AI reply to the page as it arrives, on a background thread."""

        def run() -> None:
            buffer: list[str] = []
            full: list[str] = []
            last_flush = time.monotonic()
            try:
                for chunk in chunks:
                    buffer.append(chunk)
                    full.append(chunk)
                    if time.monotonic() - last_flush >= _FLUSH_SECONDS:
                        self._call("aiChunk", {**meta, "text": "".join(buffer)})
                        buffer.clear()
                        last_flush = time.monotonic()
                if buffer:
                    self._call("aiChunk", {**meta, "text": "".join(buffer)})
                if meta.get("target") == "translation" and meta["lineId"] in self.lines:
                    self.lines[meta["lineId"]]["translation"] = "".join(full).strip()
                self._call("aiDone", meta)
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
        while True:
            self._requests.get()
            try:
                self._capture()
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

    def _capture(self) -> None:
        if self.cfg.capture.mode == "window" and capture.focused_window_is_own():
            self._status("error", "Horu has focus. Click your game, then press the hotkey.")
            return
        self._status("busy", "Reading...")
        image = capture.grab(self.cfg.capture)
        if self.cfg.save_captures:
            capture.save(image, self.cfg.captures_dir)
        game = (capture.focused_process_name() if self.cfg.capture.mode == "window" else None) or corrections.DEFAULT_GAME
        hints = dataclasses.replace(self.corrections.hints(game), known_speakers=self.speakers.known(game))
        blocks = layout.group_blocks(self._source.read(image), image, hints=hints)
        # Correct misread name tags against names already seen in this game.
        blocks = [
            dataclasses.replace(b, speaker=self.speakers.resolve(game, b.speaker, b.speaker_score)) if b.speaker else b
            for b in blocks
        ]

        # Text shown recently stays in the capture but is marked as a repeat, so the page can
        # collapse it instead of lines silently going missing (e.g. when scrolling a backlog).
        lines = []
        for block in blocks:
            repeat = block.text in self.recent
            if not repeat:
                self.recent.append(block.text)
            line = self._line(block, repeat)
            line["game"] = game
            if line["speaker"]:
                line["speaker"]["english"] = self.speaker_english(game, block.speaker)
            lines.append(line)
        new = [l for l in lines if not l["repeat"]]
        if not lines:
            self._ready("No Japanese text found.")
            return
        if not new:
            self._ready(f"Nothing new: all {len(lines)} line(s) were already shown.")
            return
        for line, block in zip(lines, blocks):
            self.lines[line["id"]] = line
            if not line["repeat"]:
                self._keep_screenshot(line["id"], image, block.box)
        self._call("addCapture", {"id": next(self._capture_ids), "time": time.strftime("%H:%M:%S"), "lines": lines})
        self._ready()
        if self.prefs.values["autoTranslate"]:
            for line in new:
                if line["kind"] == "dialogue":
                    self.translate(line["id"])
        if self.prefs.values["autoCards"] and self._provider:
            self.queue_auto_cards([line["id"] for line in new if line["kind"] == "dialogue"])

    def _line(self, block: layout.TextBlock, repeat: bool = False) -> dict:
        speaker = None
        if block.speaker:
            speaker = {"text": block.speaker, "words": self._words(block.speaker)}
        return {
            "id": next(self._ids),
            "speaker": speaker,
            "repeat": repeat,
            "text": block.text,
            "kind": block.kind,
            # OCR's confidence in its weakest line (speaker included): below this, the line may
            # be misread.
            "uncertain": min(block.speaker_score, *(l.score for l in block.lines)) < _UNCERTAIN,
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


_PERSON_NAME_TYPES = {"surname", "given name", "female given name", "male given name", "full name", "character", "fictional"}


def _speaker_text(line: dict) -> str | None:
    return line["speaker"]["text"] if line["speaker"] else None


def _pretty_hotkey(spec: str) -> str:
    return "+".join(part.strip("<>").capitalize() for part in spec.split("+"))
