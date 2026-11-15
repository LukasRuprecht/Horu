"""The Horu desktop app: a pywebview window that shows captured lines word by word."""

from __future__ import annotations

import base64
import dataclasses
import io
import itertools
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import traceback
import unicodedata
import webbrowser
from collections import OrderedDict, deque
from pathlib import Path

from horu import ai_setup, anki, anki_web, autocapture, capture, config, correction, corrections, debug_bundle, dictionary, hotkey, layout, llm, prefs, recorder, snip, speakers, text_sources, translations, tray, wordbook
from horu.nlp import analyze
from horu.text_sources import hook, rapidocr_source
from horu.text_sources.vote import _SIZES, edit_distance
from horu.tutor import Line, Tutor, clean_reading, numbered_lines

UI_DIR = Path(__file__).parent / "ui"
# Blocks already shown recently are skipped, so persistent text (HUD, menus) and repeated
# hotkey presses on the same dialogue don't flood the history.
_RECENT_LINES = 20
# A line read again is still a repeat when OCR reads it a little differently (１ for 1, 喜 for
# 暮): up to one character in this many may differ (at least one, from _SLACK_FROM characters
# on). Shorter lines must match exactly.
_REPEAT_SLACK = 10
_SLACK_FROM = 6
_FULL_SIZE = str.maketrans({small: big for big, small in _SIZES.items()})
_JAPANESE_OR_DIGIT = re.compile(r"[぀-ヿ㐀-鿿々ー0-9]")
# A piece of a line already shown this long or longer is a repeat too.
_PIECE_CHARS = 6
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
# Share of the game picture other windows may cover before auto-capture pauses.
_MOST_COVERED = 0.5
# Hotkey captures whose game picture is kept, for drawing auto-read regions on.
_PICTURES_KEPT = 10
_PICTURE_MAX_WIDTH = 1280
# Captures kept whole (screenshot and OCR lines), for saving as a debug bundle.
_BUNDLES_KEPT = 5
# Name tags guessed as a known name, kept so a wrong guess can be checked with the AI.
_GUESSES_KEPT = 50
# AI requests nobody clicked for (see _Jobs): translations of new lines first, then checks of
# what OCR was unsure of and speaker names. Requests the learner asks for don't wait.
_AUTO, _BACKGROUND = 1, 2
_AI_WORKERS = 2
# Text hookers' own windows: a line copied while one is in front belongs to the game before.
_HOOKERS = {"textractor.exe", "lunatranslator.exe", "lunahost32.exe", "lunahost64.exe", "agent.exe", "ith.exe"}


@dataclasses.dataclass
class AutoRead:
    """A capture asked for by auto-capture: the game picture, already grabbed."""

    image: object  # PIL image of the game window, as the hotkey would capture it
    game: str
    region: str = "picture"  # where it was watched, for joining a line read while still typing


@dataclasses.dataclass
class Snip:
    """A box the learner dragged on screen: read like a capture, and translated once."""

    image: object  # PIL image of the box
    game: str


@dataclasses.dataclass
class HookedText:
    """A line a text hooker sent."""

    text: str
    game: str


class _Jobs:
    """AI requests made without the learner asking, run a couple at a time, most important
    and newest first: a burst of lines doesn't flood the provider (or the learner's quota),
    and the line on screen now isn't stuck behind older ones."""

    def __init__(self, workers: int = _AI_WORKERS) -> None:
        self._queue: queue.PriorityQueue = queue.PriorityQueue()
        self._order = itertools.count()
        for _ in range(workers):
            threading.Thread(target=self._work, name="horu-ai-jobs", daemon=True).start()

    def put(self, priority: int, job) -> None:
        self._queue.put((priority, -next(self._order), job))

    def busy(self) -> bool:
        return self._queue.unfinished_tasks > 0

    def _work(self) -> None:
        while True:
            _, _, job = self._queue.get()
            try:
                job()
            except Exception:
                traceback.print_exc()
            finally:
                self._queue.task_done()


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

    def fix_speaker(self, line_id: int, text: str) -> None:
        """The learner's name for a line's speaker (after a wrong guess): remembered."""
        self._app.fix_speaker(line_id, text)

    def check_speaker(self, line_id: int) -> dict:
        """Read a guessed name tag with the AI."""
        return self._app.check_speaker(line_id)

    def game_windows(self) -> dict:
        """Open windows to choose the game from, and the one chosen now."""
        return self._app.game_windows()

    # Not "window": pywebview names the JavaScript function's parameters after these, and one
    # called window would hide the page's window object from its own bridge code.
    def set_game_window(self, window_id: int | None) -> None:
        """Capture this window for the rest of the session (None: the next one the hotkey is
        pressed in)."""
        self._app._set_game_window(window_id)

    def snip(self) -> dict:
        """Drag a box on screen; what's in it is read and translated once."""
        return self._app.snip()

    def record(self, on: bool) -> dict:
        """Start or stop recording what auto-capture sees (for evals/replay.py)."""
        return self._app.record(on)

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
        elif key in ("hookSource", "hookUrl"):
            self._app.apply_hook()
        elif key == "autoCapture" and self._app._tray:
            self._app._tray.refresh()

    def hook_state(self) -> dict:
        return self._app.hook_state

    def hotkey_info(self) -> dict:
        return self._app.hotkey_info()

    def set_hotkey(self, spec: str) -> dict:
        return self._app.set_hotkey(spec)

    def mark(self, line_id: int, label: str, part: str = "text") -> dict:
        """Remember a correction for this line's text (or its speaker) in this game."""
        return self._app.mark(line_id, label, part)

    def set_speaker_english(self, game: str, text: str, english: str) -> None:
        """The user's own English for a speaker; replaces any cached translation."""
        self._app.set_speaker_english(game, text, english)

    def translate_speaker(self, game: str, text: str) -> None:
        self._app.translate_speaker(game, text)

    def anki_status(self) -> dict:
        return {**self._app.anki.status(), "deck": self._app.prefs.values["ankiDeck"], "via": self._app.prefs.values["ankiVia"]}

    def anki_sign_in(self, email: str, password: str) -> dict:
        """Sign in to AnkiWeb (and download the collection)."""
        try:
            self._app.anki_web.sign_in(email, password)
        except anki.AnkiError as e:
            return {"ok": False, "message": str(e)}
        return {"ok": True, **self.anki_status()}

    def anki_sign_out(self) -> dict:
        self._app.anki_web.sign_out()
        return self.anki_status()

    def anki_sync(self) -> dict:
        try:
            self._app.anki_web.sync()
        except anki.AnkiError as e:
            return {"ok": False, "message": str(e)}
        return {"ok": True, **self.anki_status()}

    def save_card(self, line_id: int, index: int, part: str = "text") -> dict:
        return self._app.save_card(line_id, index, part)

    # Saved words and the Study tab

    def save_word(self, line_id: int, index: int, part: str = "text") -> dict:
        return self._app.save_word(line_id, index, part)

    def saved_words(self, query: str = "", game: str = "", status: str = "") -> dict:
        return {"words": self._app.words.words(query=query.strip(), game=game, status=status), "games": self._app.words.games()}

    def word_image(self, word_id: int) -> str | None:
        png = self._app.words.image(word_id)
        return png and "data:image/png;base64," + base64.b64encode(png).decode()

    def remove_word(self, word_id: int) -> None:
        self._app.words.remove(word_id)

    def words_to_anki(self, word_ids: list[int]) -> dict:
        return self._app.words_to_anki(word_ids)

    def study_summary(self) -> dict:
        return self._app.study_summary()

    def next_review(self) -> dict | None:
        return self._app.words.next_card(self._app.prefs.values["newPerDay"])

    def answer_review(self, word_id: int, rating: str) -> dict | None:
        self._app.words.answer(word_id, rating)
        return self.next_review()

    def review_in_anki(self) -> dict:
        try:
            self._app.anki.review_deck(self._app.prefs.values["ankiDeck"] or "Horu")
        except anki.AnkiError as e:
            return {"ok": False, "message": str(e)}
        return {"ok": True}

    def make_cards(self, line_id: int, session: int) -> None:
        self._app.make_cards(line_id, session)

    def save_debug_bundle(self, line_id: int | None, note: str = "") -> dict:
        return self._app.save_debug_bundle(line_id, note)

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
        # (window, game) Horu captures this session: the first one the hotkey was pressed in, or
        # the one chosen in the Read tab. The hotkey captures it even when another window is in
        # front; auto-capture watches it.
        self._game_window: tuple[int, str] | None = None
        self._auto_state: tuple[str, str] | None = None  # last state shown on the auto toggle
        self._last_auto: dict[str, tuple[int, str]] = {}  # region -> (line id, text) of the last line read there
        self._captured_pictures: OrderedDict[int, tuple[bytes, tuple[int, int], str]] = OrderedDict()  # capture id -> (JPEG, picture size, game)
        self._kept_captures: OrderedDict[int, debug_bundle.Capture] = OrderedDict()  # capture id -> the capture, for debug bundles
        self._started: dict[int, float] = {}  # line id -> when its capture began, for timing logs
        self._anki_connect = anki.AnkiClient()
        self.anki_web = anki_web.AnkiWebClient()
        self.words = wordbook.Wordbook()
        self._card_queue: queue.Queue[int] = queue.Queue()  # lines waiting for automatic cards
        self._card_worker: threading.Thread | None = None
        self._screenshots: OrderedDict[int, bytes] = OrderedDict()  # line id -> PNG
        self._speaker_shots: dict[int, bytes] = {}  # line id -> PNG of a doubtful name tag, until checked
        self._guessed_shots: OrderedDict[int, bytes] = OrderedDict()  # line id -> PNG of a guessed name tag
        self.translations = translations.TranslationMemory()
        self._jobs = _Jobs()
        self._speakers_pending: set[tuple[str, str]] = set()  # AI speaker translations in flight
        self.recent: deque[tuple[str, int]] = deque(maxlen=_RECENT_LINES)  # (text, line id) shown recently
        self.lines: dict[int, dict] = {}  # every line shown, in order, by id
        self._ai = ai_setup.Resolved()
        self._vision: bool | None = None  # whether the AI model reads images (None: not known yet)
        self._tutor_chat: tuple[int, llm.Chat] | None = None  # (session id, chat) in the tutor panel
        self._source: text_sources.TextSource | None = None
        self._dictionary: dictionary.Dictionary | None = None
        self._requests: queue.Queue[AutoRead | HookedText | None] = queue.Queue()  # None: the hotkey
        self._hook: hook.Listener | None = None
        self.hook_state = {"state": "off", "message": ""}
        self._ids = itertools.count(1)
        self._capture_ids = itertools.count(1)
        self._page_loaded = threading.Event()
        self._stop_hotkey = None
        self._tray: tray.Tray | None = None
        self._told_tray = False
        self._snipping = False
        self._recorder: recorder.Recorder | None = None  # while recording what auto-capture sees
        self._recording = False  # asked for; the recorder is made at the watcher's next look
        self._hotkey_error: str | None = None  # why the capture hotkey couldn't be set up

    def run(self) -> None:
        import webview

        tray.set_app_id()
        self.window = webview.create_window(
            "Horu",
            url=str(UI_DIR / "index.html"),
            js_api=Api(self),
            width=560,
            height=900,
            min_size=(360, 400),
            on_top=self.prefs.values["onTop"],
            # The page's own background, so the window doesn't flash another colour while loading.
            background_color="#f4f2ec" if self.prefs.values["colorMode"] == "light" else "#15171d",
        )
        self.window.events.loaded += self._page_loaded.set
        self.window.events.minimized += self._minimized
        self._tray = self._start_tray()
        _quit_on_ctrl_c()
        # webview.start blocks on the main thread; _worker runs on a background thread, a
        # daemon one so closing the window ends Horu (it waits for captures forever).
        start_worker = lambda: threading.Thread(target=self._worker, name="horu-worker", daemon=True).start()
        webview.start(start_worker, debug=self.debug)
        if self._tray:
            self._tray.stop()
        if self._stop_hotkey:
            self._stop_hotkey()

    def _start_tray(self) -> tray.Tray | None:
        """The tray icon; Horu works without one (a system with no tray, a failed start)."""
        try:
            icon = tray.Tray(
                show=self.show_window,
                capture=self._capture_from_tray,
                auto_on=lambda: bool(self.prefs.values["autoCapture"]),
                toggle_auto=lambda: self.window.evaluate_js("document.querySelector('#toggle-auto').click()"),
                quit=lambda: self.window.destroy(),
            )
            icon.start()
            return icon
        except Exception:
            traceback.print_exc()
            return None

    def show_window(self) -> None:
        self.window.show()
        # Only from minimized: restoring a maximized (or snapped) window would shrink it.
        if capture.is_minimized(self.window.native.Handle.ToInt32()):
            self.window.restore()

    def _minimized(self) -> None:
        if self._tray and self.prefs.values["minimizeToTray"]:
            self.window.hide()
            if not self._told_tray:  # once a run: say where the window went
                self._told_tray = True
                self._tray.notify("Horu is still running. Click its icon by the clock (under ^) to bring it back.")

    def _capture_from_tray(self) -> None:
        # The tray has focus now, not the game: only a chosen game window can be captured.
        if self._game_window is None:
            self.show_window()
            self._status("error", f"Press {_pretty_hotkey(self.hotkey_spec)} in your game once, or choose your game in the Read tab.")
        else:
            self._requests.put(None)

    @property
    def anki(self) -> anki.AnkiClient | anki_web.AnkiWebClient:
        """Anki as the learner set it up: through AnkiWeb, or the Anki app on this PC."""
        return self.anki_web if self.prefs.values["ankiVia"] == "web" else self._anki_connect

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

    def _written_card(self, line_id: int, index: int, part: str) -> anki.Card:
        """A card for one word. With an AI provider, its meaning and note are written for this
        line; otherwise the card uses the dictionary meaning."""
        card = self.card_for(line_id, index, part)
        if self._provider:
            line = self.lines[line_id]
            word = (line["speaker"]["words"] if part == "speaker" else line["words"])[index]
            try:
                meaning, note = self._tutor().card_note(self._line_context(line_id), word)
            except llm.AIError:
                meaning, note = "", ""  # the dictionary meaning is still a good card
            card.meaning = meaning or card.meaning
            card.notes = "; ".join(filter(None, [note, card.notes]))
        return card

    def save_card(self, line_id: int, index: int, part: str = "text") -> dict:
        """Add one word to Anki as a card."""
        if line_id not in self.lines:
            return {"ok": False, "message": "That line is no longer available."}
        card = self._written_card(line_id, index, part)
        deck = self.prefs.values["ankiDeck"] or "Horu"
        try:
            self.anki.add_card(deck, card, self._screenshots.get(line_id))
        except anki.AnkiError as e:
            return {"ok": False, "message": str(e)}
        return {"ok": True, "message": f"Added {card.dictionary_form} to {deck}."}

    def save_word(self, line_id: int, index: int, part: str = "text") -> dict:
        """Save one word in Horu, with its line and screenshot, for the Study tab."""
        if line_id not in self.lines:
            return {"ok": False, "message": "That line is no longer available."}
        line = self.lines[line_id]
        word = (line["speaker"]["words"] if part == "speaker" else line["words"])[index]
        card = self._written_card(line_id, index, part)
        saved, new = self.words.add(
            word=card.dictionary_form,
            reading=card.reading,
            surface=card.word,
            meaning=card.meaning,
            notes=card.notes,
            pos=word["pos"],
            jlpt=word.get("jlpt"),
            sentence=card.sentence,
            speaker=line["speaker"]["text"] if line["speaker"] and part != "speaker" else "",
            translation=card.translation,
            game=line["game"],
            screenshot_png=self._screenshots.get(line_id),
        )
        if not new:
            return {"ok": True, "message": f"{card.dictionary_form} is already in your words.", "id": saved["id"]}
        return {"ok": True, "message": f"Saved {card.dictionary_form}.", "id": saved["id"]}

    def words_to_anki(self, word_ids: list[int]) -> dict:
        """Add saved words to Anki as cards; words already in the deck are skipped."""
        deck = self.prefs.values["ankiDeck"] or "Horu"
        added = skipped = 0
        for word_id in word_ids:
            w = self.words.get(word_id)
            if w is None:
                continue
            source = " · ".join(filter(None, [w["speaker"], w["game"].removesuffix(".exe"), time.strftime("%Y-%m-%d", time.localtime(w["added"]))]))
            card = anki.Card(
                sentence=w["sentence"], word=w["surface"], dictionary_form=w["word"], reading=w["reading"],
                meaning=w["meaning"], notes=w["notes"], translation=w["translation"], source=source,
            )
            try:
                self.anki.add_card(deck, card, self.words.image(word_id))
                added += 1
            except anki.AnkiError as e:
                if "already in your" not in str(e):
                    return {"ok": False, "message": str(e), "added": added}
                skipped += 1
        message = f"Added {added} {'card' if added == 1 else 'cards'} to {deck}."
        if skipped:
            message += f" {skipped} {'was' if skipped == 1 else 'were'} already there."
        return {"ok": True, "message": message, "added": added}

    def study_summary(self) -> dict:
        summary = self.words.summary(self.prefs.values["newPerDay"])
        if self.prefs.values["studyWith"] == "anki":
            deck = self.prefs.values["ankiDeck"] or "Horu"
            via = self.prefs.values["ankiVia"]
            try:
                if via == "web" and not self.anki_web.signed_in:
                    raise anki.AnkiError("Sign in to AnkiWeb in Settings → Study.")
                summary["anki"] = {"connected": True, "deck": deck, "via": via, **self.anki.due_counts(deck)}
            except anki.AnkiError as e:
                summary["anki"] = {"connected": False, "deck": deck, "via": via, "error": str(e)}
        return summary

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
            self.recent.append((text, line_id))  # read like this again, it's a repeat
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

        self._jobs.put(_BACKGROUND, run)

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

    def translate(self, line_id: int, check: bool = False, priority: int | None = None, cards: bool = False) -> None:
        """Translate a line. `check`: send its screenshot too, so the same request checks
        OCR's reading (and the translation follows what the image shows). `priority`: queued
        (see _Jobs) rather than sent now. `cards`: make automatic cards once it's done."""
        if not (self._provider and line_id in self.lines):
            return
        png = self._screenshots.get(line_id) if check and self._can_check(line_id) else None
        chunks = self._tutor().translate(self._line_context(line_id), png)
        meta = {"target": "translation", "lineId": line_id, "gen": self._next_translation(line_id)}
        self._stream({**meta, "check": png is not None, "cards": cards}, chunks, priority)
        self._translate_name(line_id)

    def _next_translation(self, line_id: int) -> int:
        # Numbered, so the page and the stored translation keep only the latest one when a
        # fixed line is translated again while the first translation still streams.
        line = self.lines[line_id]
        line["translationGen"] = line.get("translationGen", 0) + 1
        return line["translationGen"]

    def _translate_name(self, line_id: int) -> None:
        """The speaker's English, unless the name tag is still to be checked: a misread
        name would get its own translation."""
        line = self.lines[line_id]
        if line["speaker"] and not (line_id in self._speaker_shots and self._can_check(line_id)):
            self.translate_speaker(line["game"], line["speaker"]["text"])

    def _translate_together(self, line_ids: list[int]) -> None:
        """Lines that appeared together (a choice under a line): one request, a numbered
        reply line each. Lines missing from the reply are translated on their own."""
        chunks = self._tutor().translate_lines([self._line_context(i) for i in line_ids])
        if chunks is None:
            for line_id in line_ids:
                self.translate(line_id, priority=_AUTO)
            return
        metas = {
            n: {"target": "translation", "lineId": line_id, "gen": self._next_translation(line_id)}
            for n, line_id in enumerate(line_ids, 1)
        }
        for line_id in line_ids:
            self._translate_name(line_id)

        def run() -> None:
            reply, done = [], set()

            def finish(text: str) -> None:
                for n, translation in numbered_lines(text).items():
                    if n in metas and n not in done:
                        done.add(n)
                        meta = metas[n]
                        self._call("aiChunk", {**meta, "text": translation})
                        self._keep_translation(meta, translation, verified=False)
                        self._call("aiDone", meta)
                        self._time(meta["lineId"], "translated")

            try:
                for chunk in chunks:
                    reply.append(chunk)
                    text = "".join(reply)
                    if "\n" in text:
                        finish(text.rsplit("\n", 1)[0])  # lines complete so far
                finish("".join(reply))
            except llm.AIError as e:
                for n, meta in metas.items():
                    if n not in done:
                        self._call("aiError", {**meta, "message": str(e)})
                return
            for n, meta in metas.items():
                if n not in done:
                    self.translate(meta["lineId"], priority=_AUTO)

        self._jobs.put(_AUTO, run)

    def _keep_translation(self, meta: dict, translation: str, verified: bool) -> None:
        """Store a finished translation on its line, and remember it for this game."""
        line = self.lines.get(meta["lineId"])
        if line is None or meta["gen"] != line.get("translationGen"):
            return
        line["translation"] = translation
        speaker = line["speaker"]["text"] if line["speaker"] else None
        self.translations.put(line["game"], speaker, line["text"], translation, verified or not line["uncertain"])

    def _remembered(self, line: dict) -> bool:
        """Show a translation made earlier for the same line in this game, if there is one.
        A line OCR was unsure of takes only one made after the AI checked that same text."""
        speaker = line["speaker"]["text"] if line["speaker"] else None
        found = self.translations.get(line["game"], speaker, line["text"])
        if found is None or (line["uncertain"] and not found[1]):
            return False
        line["translation"] = found[0]
        line["uncertain"] = False  # read exactly as text the AI checked before
        return True

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

    def _stream(self, meta: dict, chunks, priority: int | None = None) -> None:
        """Push an AI reply to the page as it arrives, on a background thread (or queued, with
        `priority`). A translation with "check" set had the line's screenshot: its reply also
        settles OCR's reading."""

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
                outcome = None
                if meta.get("check"):
                    outcome = self._checked(meta["lineId"], proposal)
                elif proposal:
                    outcome = self.propose(meta["lineId"], proposal, "explain")
                if meta.get("target") == "translation" and outcome != correction.SUGGEST:
                    self._keep_translation(meta, correction.without_proposal(reply), verified=bool(meta.get("check")))
                elif meta.get("target") == "translation" and meta["lineId"] in self.lines:
                    line = self.lines[meta["lineId"]]
                    if meta["gen"] == line.get("translationGen"):
                        line["translation"] = correction.without_proposal(reply)  # of the suggested text
                self._call("aiDone", meta)
                if meta.get("target") == "translation":
                    self._time(meta["lineId"], "translated")
            except llm.AIError as e:
                if meta.get("check") and "can't read images" in str(e):
                    # A text-only model: translate without the screenshot, and stop sending them.
                    self._vision = False
                    self.translate(meta["lineId"], priority=priority, cards=meta.get("cards", False))
                    return
                self._call("aiError", {**meta, "message": str(e)})
            except Exception as e:
                traceback.print_exc()
                self._call("aiError", {**meta, "message": f"Something went wrong: {e}"})
            if meta.get("cards"):
                self._auto_cards([meta["lineId"]])

        if priority is None:
            threading.Thread(target=run, name="horu-ai", daemon=True).start()
        else:
            self._jobs.put(priority, run)

    def _checked(self, line_id: int, proposal: str | None) -> str | None:
        """Act on a translation made with the line's screenshot: no "OCR:" line means the
        model read what OCR read."""
        line = self.lines.get(line_id)
        if line is None:
            return None
        reading = clean_reading(proposal) if proposal else line["text"]
        line["aiReading"] = reading
        self._time(line_id, "checked")
        return self.propose(line_id, reading, "vision", translated=True)

    # --- background work -------------------------------------------------------------

    def _worker(self) -> None:
        self._page_loaded.wait()
        self.apply_capture_exclusion()
        tray.set_window_icon(self.window.native.Handle.ToInt32())
        if not self._load():
            return
        threading.Thread(target=self._watch, name="horu-watch", daemon=True).start()
        while True:
            request = self._requests.get()
            try:
                if isinstance(request, HookedText):
                    self._show_hooked(request)
                else:
                    self._capture(request)
            except Exception as e:  # keep the app alive; show what went wrong
                traceback.print_exc()
                self._status("error", f"Capture failed: {e}")

    def _load(self) -> bool:
        shown = [None]

        def show(message: str) -> None:  # downloads report often; only changes reach the page
            if message != shown[0]:
                shown[0] = message
                self._status("loading", message)

        # Short: the status bar is narrow. These happen on the first run only.
        def dictionary_progress(name: str, done: int, total: int) -> None:
            what = "dictionary" if name == "JMdict" else "names dictionary"
            if total and done < total:
                show(f"Downloading {what}: {done >> 20}/{total >> 20} MB")
            else:
                show(f"Indexing {what}...")

        def ocr_progress(done: int, total: int) -> None:
            if total and done < total:
                show(f"Downloading OCR model: {done >> 20}/{total >> 20} MB")
            else:
                show(f"Loading OCR ({self.cfg.ocr_engine})...")

        try:
            try:
                self._dictionary = dictionary.Dictionary()
            except dictionary.DictionaryMissing:
                show("Downloading the dictionary (first run only)...")
                dictionary.build(progress=dictionary_progress)
                self._dictionary = dictionary.Dictionary()
            if not self._dictionary.has_names:  # dictionaries built before names were added
                show("Downloading the names dictionary (one time only)...")
                dictionary.build_names(progress=dictionary_progress)
                self._dictionary = dictionary.Dictionary()
            show(f"Loading OCR ({self.cfg.ocr_engine})...")
            with rapidocr_source.download_progress(ocr_progress):
                self._source = text_sources.create(self.cfg.ocr_engine)
            self.apply_ai()  # AI is optional: problems are reported in settings, not fatal
            self.apply_hook()
            if self.prefs.values["ankiVia"] == "web" and self.anki_web.signed_in:
                self.anki_web.sync_soon(delay=0)
        except Exception as e:
            traceback.print_exc()
            self._status("error", str(e))
            return False
        # Without the hotkey, auto-capture and a text hooker still work: say how to fix it.
        try:
            self._stop_hotkey = hotkey.listen(self.hotkey_spec, lambda: self._requests.put(None))
        except hotkey.HotkeyError as e:
            self._hotkey_error = str(e).replace(self.hotkey_spec, _pretty_hotkey(self.hotkey_spec))
        self._call("showHotkey", self.hotkey_info())
        self._ready()
        return True

    @property
    def hotkey_spec(self) -> str:
        return self.prefs.values["captureHotkey"] or self.cfg.capture_hotkey

    def hotkey_info(self) -> dict:
        return {"spec": self.hotkey_spec, "name": _pretty_hotkey(self.hotkey_spec), "error": self._hotkey_error}

    def set_hotkey(self, spec: str) -> dict:
        """Use `spec` as the capture hotkey, if Windows lets Horu have it; the old one stays
        otherwise."""
        if spec == self.hotkey_spec and self._stop_hotkey:
            return {"ok": True, "message": f"Capture with {_pretty_hotkey(spec)}.", **self.hotkey_info()}
        try:
            stop = hotkey.listen(spec, lambda: self._requests.put(None))
        except hotkey.HotkeyError as e:
            return {"ok": False, "message": f"{str(e).replace(spec, _pretty_hotkey(spec))} Try another.", **self.hotkey_info()}
        if self._stop_hotkey:
            self._stop_hotkey()
        self._stop_hotkey, self._hotkey_error = stop, None
        self.prefs.set("captureHotkey", spec)
        self._ready()
        return {"ok": True, "message": f"Capture with {_pretty_hotkey(spec)}.", **self.hotkey_info()}

    def _capture(self, request: AutoRead | Snip | None = None) -> None:
        started = time.monotonic()
        auto = isinstance(request, AutoRead)
        snipped = isinstance(request, Snip)
        if snipped:
            image, game = request.image, request.game
            self._status("busy", "Reading...")
        elif auto:
            image, game = request.image, request.game
        elif self.cfg.capture.mode == "window" and self._game_window and capture.window_box(self._game_window[0]):
            window, game = self._game_window
            grabbed = capture.grab_window(window, own_ok=self.prefs.values["hideFromCapture"])
            if grabbed is None or grabbed[1] > _MOST_COVERED:
                self._status("error", f"Something is covering {_game_label(game)}. Bring it to the front, then press the hotkey.")
                return
            self._status("busy", "Reading...")
            image = grabbed[0]
        else:
            if self.cfg.capture.mode == "window" and capture.focused_window_is_own():
                self._status("error", "Horu has focus. Click your game, then press the hotkey (or choose the game in the Read tab).")
                return
            self._status("busy", "Reading...")
            image = capture.grab(self.cfg.capture)
            game = (capture.focused_process_name() if self.cfg.capture.mode == "window" else None) or corrections.DEFAULT_GAME
            window = capture.foreground_window() if self.cfg.capture.mode == "window" else None
            if window:
                self._set_game_window(window, game)
        if self.cfg.save_captures:
            capture.save(image, self.cfg.captures_dir)
        hints = dataclasses.replace(self.corrections.hints(game), known_speakers=self.speakers.known(game))
        read = self._source.read(image)
        blocks = layout.group_blocks(read, image, hints=hints)
        capture_id = next(self._capture_ids)
        kept = debug_bundle.Capture(image, read, game, auto, hints)
        if not auto or blocks:  # auto-capture also fires on boxes emptying: nothing to report there
            self._kept_captures[capture_id] = kept
            while len(self._kept_captures) > _BUNDLES_KEPT:
                self._kept_captures.popitem(last=False)
        # Correct misread name tags against names already seen in this game, and guess the
        # name an unsure read is close to (the page offers to fix a wrong guess).
        guessed: dict[int, str] = {}  # block index -> what OCR read
        for k, b in enumerate(blocks):
            if not b.speaker:
                continue
            name = self.speakers.resolve(game, b.speaker, b.speaker_score)
            if not self.speakers.confirmed(game, name):
                alternatives = tuple(b.speaker_line.alternatives) if b.speaker_line else ()
                guess = self.speakers.guess(game, name, b.speaker_score, alternatives)
                if guess:
                    guessed[k], name = b.speaker, guess
            blocks[k] = dataclasses.replace(b, speaker=name)

        # A hotkey capture's game picture, for drawing auto-read regions on.
        picture = autocapture.picture_box(image) if not (auto or snipped) else None
        if auto:
            # Only dialogue: a sign the camera stopped on, a location name, or a menu would be
            # read some of the time. The hotkey reads those.
            blocks = [b for b in blocks if b.kind == "dialogue"]

        # Text shown recently stays in the capture but is marked as a repeat, so the page can
        # collapse it instead of lines silently going missing (e.g. when scrolling a backlog).
        lines = []
        for k, block in enumerate(blocks):
            earlier = next((i for text, i in self.recent if _same_line(block.text, text)), None)
            # A snip is asked for: everything in it is shown, even if seen before.
            repeat = not snipped and (earlier is not None or _pieces_of(block.text, [text for text, _ in self.recent]))
            line = self._line(block, repeat, game)
            line["game"], line["captureId"] = game, capture_id
            if snipped:
                line["snip"] = True
            if not repeat and earlier is None:
                self.recent.append((block.text, line["id"]))
            if earlier is not None and self.lines.get(earlier, {}).get("translation"):
                line["translation"] = self.lines[earlier]["translation"]  # shown with it, no new request
            if picture is not None:  # where it was, for drawing auto-read regions
                box, size = autocapture.in_picture(block.box, picture)
                line["capture"], line["box"], line["frame"], line["rows"] = capture_id, list(box), list(size), len(block.lines)
            if line["speaker"]:
                line["speaker"]["english"] = self.speaker_english(game, block.speaker)
                if k in guessed:
                    line["speaker"].update(was=guessed[k], guessed=True)
            lines.append(line)
        kept.shown = lines
        if self._recorder and not snipped:
            self._recorder.note("read", auto=auto, lines=[
                {"text": l["text"], "kind": l["kind"], "repeat": l["repeat"], "speaker": _speaker_text(l)} for l in lines
            ])
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
        if picture is not None:
            self._keep_picture(capture_id, image, picture, game)
        for line, block in zip(lines, blocks):
            self.lines[line["id"]] = line
            if not line["repeat"]:
                self._keep_screenshot(line["id"], image, block.box)
                if line["speaker"] and line["speaker"]["uncertain"]:
                    self._speaker_shots[line["id"]] = _png(image, block.speaker_line.box, margin=8)
                elif line["speaker"] and line["speaker"].get("guessed") and block.speaker_line:
                    self._guessed_shots[line["id"]] = _png(image, block.speaker_line.box, margin=8)
                    while len(self._guessed_shots) > _GUESSES_KEPT:
                        self._guessed_shots.popitem(last=False)
                if line["kind"] == "dialogue" and self._provider and self.prefs.values["autoTranslate"] and not snipped:
                    self._remembered(line)
        self._call("addCapture", {"id": capture_id, "time": time.strftime("%H:%M:%S"), "lines": lines, "auto": auto, "snip": snipped})
        for line in new:
            self._started[line["id"]] = started
            self._time(line["id"], "shown" + (" (flagged)" if line["uncertain"] else ""))
        if snipped:
            self._ready(None if self._provider else "Set up AI in Settings → AI to translate snips.")
            self._translate_snip(new)
            return
        self._ready()
        dialogue_new = [l for l in new if l["kind"] == "dialogue"]
        if auto:
            if dialogue_new:
                self._last_auto[request.region] = (dialogue_new[-1]["id"], dialogue_new[-1]["text"])
            else:
                self._last_auto.pop(request.region, None)
        self._ai_for(new)

    def _ai_for(self, new: list[dict]) -> None:
        """Ask the AI what new lines need, in as few requests as possible:
        - dialogue is translated; a line OCR was unsure of goes with its screenshot, so the
          same request checks the reading (and the translation follows what the image shows);
          lines that appeared together go in one request; lines translated before in this
          game reuse that translation;
        - other text (menus, labels) OCR was unsure of, and dialogue it was unsure of when
          translations are off, is checked from its screenshot;
        - name tags not yet confirmed are checked (names close to a confirmed one were
          guessed instead).
        Lines OCR was sure of aren't checked: on the truth screens its sure lines were right
        but for a dropped 。 or ぉ, and the translation can still propose a fix. Cards for
        lines OCR was unsure of wait for the check, so they never teach a misreading."""
        if not self._provider:
            return
        dialogue = [line["id"] for line in new if line["kind"] == "dialogue"]
        unsure = {i for i in dialogue if self.lines[i]["uncertain"] and self._can_check(i)}
        checks = [line["id"] for line in new if line["kind"] != "dialogue" and line["uncertain"]]
        if self.prefs.values["autoTranslate"]:
            todo = [i for i in dialogue if not self.lines[i].get("translation")]
            plain = [i for i in todo if i not in unsure]
            if len(plain) > 1:
                self._translate_together(plain)
            elif plain:
                self.translate(plain[0], priority=_AUTO)
            for line_id in todo:
                if line_id in unsure:
                    self.translate(line_id, check=True, priority=_AUTO, cards=True)
            self._auto_cards([i for i in dialogue if i not in unsure or i not in todo])
        else:
            checks = sorted(unsure) + checks
            self._auto_cards([i for i in dialogue if i not in unsure])
        for line_id in checks:
            if self._can_check(line_id):
                self._jobs.put(_BACKGROUND, lambda i=line_id: self._check_lines([i], [i] if i in unsure else []))
        for line in new:
            if line["id"] in self._speaker_shots and self._can_check(line["id"]):
                self._jobs.put(_BACKGROUND, lambda i=line["id"]: self._check_name(i))

    def _translate_snip(self, new: list[dict]) -> None:
        """Every line of a snip is translated, other text too and whatever autoTranslate says:
        it was asked for. Lines OCR was unsure of go with their screenshot."""
        if not self._provider:
            return
        todo = [line["id"] for line in new if not line.get("translation")]
        unsure = [i for i in todo if self.lines[i]["uncertain"] and self._can_check(i)]
        plain = [i for i in todo if i not in unsure]
        if len(plain) > 1:
            self._translate_together(plain)
        elif plain:
            self.translate(plain[0])
        for line_id in unsure:
            self.translate(line_id, check=True)

    def snip(self) -> dict:
        """Hide Horu, let the learner drag a box on screen, and read what's in it."""
        if self._source is None:
            return {"ok": False, "message": "Horu is still starting up. Try again in a moment."}
        if self._snipping:
            return {"ok": True}
        self._snipping = True
        # Over the game's window once Horu knows it (from a hotkey capture); before that,
        # every monitor, as the game needn't be on the screen Horu is on.
        game_box = self._game_window and capture.window_box(self._game_window[0])
        area = game_box or capture.whole_screen()
        try:
            self.window.hide()
            time.sleep(0.35)  # let it fade out
            image = capture.grab_area(area)
            chosen = snip.select(image, area)
        finally:
            self.show_window()
            self._snipping = False
        if chosen is None:
            return {"ok": True}
        if game_box:
            game = self._game_window[1]
        else:
            # The game is the program under the box (a snip can be of anything on screen).
            x = area["left"] + round((chosen[0] + chosen[2]) / 2 * area["width"])
            y = area["top"] + round((chosen[1] + chosen[3]) / 2 * area["height"])
            under = capture.window_at(x, y)
            game = under and capture.process_name(under)
            if not game or under == self.window.native.Handle.ToInt32():
                game = corrections.DEFAULT_GAME
        self._requests.put(Snip(snip.crop(image, chosen), game))
        return {"ok": True}

    def _ai_busy(self) -> bool:
        """Whether queued AI work is waiting or running (for evals/eval_ai.py)."""
        return self._jobs.busy()

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
        # The page translates the full line again; a line OCR was unsure of needs its own check.
        if line["uncertain"] and self._can_check(last_id):
            self._jobs.put(_BACKGROUND, lambda: self._check_lines([last_id], []))
        return True

    # --- text hookers ------------------------------------------------------------------

    def apply_hook(self) -> None:
        """Listen to the text hooker chosen in settings (or stop listening)."""
        if self._hook:
            self._hook.stop()
            self._hook = None
        source = self.prefs.values["hookSource"]
        if source == "clipboard":
            self._hook = hook.ClipboardListener(self._hooked, self._set_hook_state, ignore=capture.focused_window_is_own)
        elif source == "websocket":
            url = self.prefs.values["hookUrl"].strip() or hook.DEFAULT_URL
            self._hook = hook.WebSocketListener(self._hooked, self._set_hook_state, url)
        else:
            self._set_hook_state("off", "")
            return
        self._hook.start()

    def _hooked(self, text: str) -> None:
        """A line from the hooker (on its thread): shown in turn with captures."""
        self._requests.put(HookedText(text, self._hook_game()))

    def _hook_game(self) -> str:
        """The game a hooked line came from: the program in front, unless that's Horu or the
        hooker (whose own window may be in front); then the game last captured."""
        if not capture.focused_window_is_own():
            name = capture.focused_process_name()
            if name and name.lower() not in _HOOKERS:
                return name
        return self._game_window[1] if self._game_window else corrections.DEFAULT_GAME

    def _set_hook_state(self, state: str, message: str) -> None:
        self.hook_state = {"state": state, "message": message}
        self._call("setHookState", self.hook_state)
        if state == "error":
            self._status("error", message)

    def _show_hooked(self, request: HookedText) -> None:
        """Show a hooked line as dialogue. The hooker's text is exact, so there's nothing to
        check, and a line already shown recently (hookers often send one twice) is skipped."""
        hooked = hook.parse(request.text)
        if hooked is None or any(hooked.text == text for text, _ in self.recent):
            return
        game = request.game
        block = layout.TextBlock((text_sources.TextLine(hooked.text, (0, 0, 0, 0)),), speaker=hooked.speaker)
        line = self._line(block, game=game)
        line["game"], line["hooked"] = game, True
        if line["speaker"]:
            line["speaker"]["english"] = self.speaker_english(game, hooked.speaker)
        self.recent.append((hooked.text, line["id"]))
        self.lines[line["id"]] = line
        remembered = bool(self._provider) and self.prefs.values["autoTranslate"] and self._remembered(line)
        self._call("addCapture", {"id": next(self._capture_ids), "time": time.strftime("%H:%M:%S"), "lines": [line], "auto": True, "hooked": True})
        if self.prefs.values["autoTranslate"] and self._provider and not remembered:
            self.translate(line["id"], priority=_AUTO)
        self._auto_cards([line["id"]])

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
                fresh = key != watching
                if fresh:
                    if self._find_text is None:
                        self._find_text = rapidocr_source.text_finder(autocapture.MAP_WIDTH)
                    watcher, watching = autocapture.TextWatcher(self._find_text), key
                fired = watcher.update(image.crop(picture), regions)
                if fired:
                    # Read like a hotkey capture: the whole window, so it reads the same.
                    self._requests.put(AutoRead(image, game))
                self._record_look(image, picture, regions, fired, fresh, game)
            except Exception:
                traceback.print_exc()

    def _watch_frame(self) -> tuple[tuple, object, autocapture.Pixels, str, list[autocapture.Box]] | None:
        """(key, image of the game window, where the game picture is in it, game, the regions to
        limit watching to) when there is something to watch now."""
        if not self.prefs.values["autoCapture"] or self._source is None:
            self._set_auto("off", "Auto-capture is off.")
            return None
        hotkey_name = _pretty_hotkey(self.hotkey_spec)
        if self._game_window is None:
            self._set_auto("waiting", f"Press {hotkey_name} in your game once, or choose your game in the Read tab, so Horu knows which window to watch.")
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
        # Watched while most of it shows: what covers the rest (a notification, the edge of
        # Horu's window) is blanked out, so it's never read. With regions, only they count.
        parts = [autocapture.to_pixels(r.box, picture) for r in regions] or [picture]
        covers = [capture.covered(window, box, part, own_ok=self.prefs.values["hideFromCapture"]) for part in parts]
        if any(share > _MOST_COVERED for share, _ in covers):
            self._set_auto("paused", f"Paused: something is covering {game}.")
            return None
        blanks = [rect for _, rects in covers for rect in rects]
        if regions:
            self._set_auto("watching", f"Watching {game}: {', '.join(r.name for r in regions)}.")
        else:
            self._set_auto("watching", f"Watching {game} for new dialogue.")
        boxes = [r.box for r in regions]
        image = capture.grab_part(box, (0, 0, *size))
        for rect in blanks:
            image.paste((0, 0, 0), rect)
        return (game, picture, tuple(boxes)), image, picture, game, boxes

    def _set_game_window(self, window: int | None, game: str | None = None) -> None:
        """The window this session captures (None: the next one the hotkey is pressed in)."""
        if window:
            game = game or capture.process_name(window) or corrections.DEFAULT_GAME
            self._game_window = (window, game)
            self._call("setGame", {"id": window, "game": game, "label": _game_label(game), "title": capture.window_title(window)})
        else:
            self._game_window = None
            self._call("setGame", None)

    def game_windows(self) -> dict:
        current = self._game_window[0] if self._game_window else None
        return {"windows": capture.game_windows(), "current": current}

    def _time(self, line_id: int, event: str) -> None:
        """Log how long after its capture began a line reached `event`, to find slow steps."""
        if line_id in self._started:
            print(f"[timing] line {line_id}: {event} at {time.monotonic() - self._started[line_id]:.2f}s", flush=True)

    def _set_auto(self, state: str, message: str) -> None:
        if self._auto_state != (state, message):
            self._auto_state = (state, message)
            self._call("setAutoState", {"state": state, "message": message})
            if self._recorder:
                self._recorder.note("auto", state=state, message=message)

    def record(self, on: bool) -> dict:
        """Start or stop recording what auto-capture sees. The recording itself starts with
        the next look at the game, so its folder is named after the game being watched."""
        if on and self._recorder is None and not self._recording:
            self._recording = True
            if not self.prefs.values["autoCapture"]:
                return {"ok": True, "recording": True, "message": "Recording starts once auto-capture (the eye button) is on."}
            return {"ok": True, "recording": True, "message": "Recording what auto-capture sees. Play as usual, then stop it in Settings → Help."}
        if not on and self._recording:
            return {"ok": True, "recording": False, "message": self._stop_recording()}
        return {"ok": True, "recording": self._recording, "message": ""}

    def _record_look(self, image, picture: autocapture.Pixels, regions: list[autocapture.Box], fired: bool, fresh: bool, game: str) -> None:
        if not self._recording:
            return
        if self._recorder is None:
            self._recorder = recorder.Recorder(game)
        self._recorder.look(image, picture, regions, fired, fresh)
        if self._recorder.expired:
            self._status("ready", self._stop_recording())
            self._call("setRecording", False)

    def _stop_recording(self) -> str:
        self._recording = False
        kept, self._recorder = self._recorder, None
        if kept is None:
            return "Nothing was recorded: auto-capture never looked at a game."
        kept.close()
        return f"Recording saved: {kept.seconds / 60:.0f} min, {kept.bytes / 1e6:.0f} MB, in {kept.path.resolve()}"

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

    # --- debug bundles ----------------------------------------------------------------

    def save_debug_bundle(self, line_id: int | None, note: str = "") -> dict:
        """Save the capture `line_id` came from (or the last capture) as a debug bundle, and
        show it in Explorer."""
        if line_id is None:
            capture_id = next(reversed(self._kept_captures), None)
        else:
            capture_id = self.lines.get(line_id, {}).get("captureId")
        kept = self._kept_captures.get(capture_id)
        if kept is None:
            if line_id is None:
                return {"ok": False, "message": "Nothing captured yet. Capture the screen with the problem, then try again."}
            return {"ok": False, "message": f"Only the last {_BUNDLES_KEPT} captures are kept. Capture the screen again, then try again."}
        settings = {
            "capture_mode": self.cfg.capture.mode,
            "ocr_engine": self.cfg.ocr_engine,
            "config_ai_provider": self.cfg.ai.provider,
            # Not the server address: it can hold a private host or a token.
            "prefs": {k: v for k, v in self.prefs.values.items() if k != "aiBaseUrl"},
        }
        log = ""
        if getattr(sys.stdout, "name", None) and Path(sys.stdout.name).name == "horu.log":  # started from the shortcut
            sys.stdout.flush()
            log = "\n".join(Path(sys.stdout.name).read_text(encoding="utf-8", errors="replace").splitlines()[-200:])
        path = debug_bundle.save(kept, note.strip(), settings, log=log)
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(path.resolve())])
        return {"ok": True, "message": f"Saved {path}. Look at the screenshot in it before sharing.", "path": str(path.resolve())}

    # --- fixing misread lines --------------------------------------------------------

    def _can_check(self, line_id: int) -> bool:
        return bool(
            self.prefs.values["aiChecks"] and self._provider and line_id in self._screenshots and self._vision is not False
        )

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
        """Have a vision model read the line from its screenshot."""
        self._check_text(line_id)

    def _check_name(self, line_id: int) -> None:
        """Check a doubtful name tag, then translate the name it settles on."""
        try:
            self._check_speaker(line_id)
        except llm.AIError as e:
            self._time(line_id, f"speaker check failed: {e}")
        line = self.lines.get(line_id)
        if line and line["speaker"] and self.prefs.values["autoTranslate"]:
            self.translate_speaker(line["game"], line["speaker"]["text"])

    def check_speaker(self, line_id: int) -> dict:
        """Read a guessed name tag with the AI (the learner thinks the guess is wrong)."""
        line = self.lines.get(line_id)
        png = self._guessed_shots.get(line_id)
        if not line or not line["speaker"]:
            return {"ok": False, "message": "That line is gone."}
        if not self._provider:
            return {"ok": False, "message": "Set up an AI provider in Settings to check names."}
        if png is None:
            return {"ok": False, "message": "The screenshot of this name is no longer kept."}
        speaker, game = line["speaker"], line["game"]
        try:
            reading = self._tutor().reread(Line(speaker["was"], alternatives=[speaker["text"]]), png)
        except llm.AIError as e:
            return {"ok": False, "message": str(e)}
        if not reading:
            return {"ok": False, "message": "The AI didn't return a name."}
        self._guessed_shots.pop(line_id, None)
        self.speakers.confirm(game, reading, was=speaker["was"])
        self.set_speaker(line_id, reading, was=speaker["was"] if reading != speaker["was"] else None)
        same = reading == speaker["text"]
        return {"ok": True, "message": f"The AI read 「{reading}」{' too' if same else ''}."}

    def fix_speaker(self, line_id: int, text: str) -> None:
        """The learner's own name for this line's speaker: remembered, so the same misread
        maps to it from now on."""
        line = self.lines.get(line_id)
        text = text.strip()
        if not line or not line["speaker"] or not text:
            return
        ocr = line["speaker"].get("was") or line["speaker"]["text"]
        self._guessed_shots.pop(line_id, None)
        self.speakers.confirm(line["game"], text, was=ocr)
        self.set_speaker(line_id, text, was=ocr if ocr != text else None)

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
        line["speaker"].update(
            text=text, words=self._words(text), english=self.speaker_english(game, text), was=was, guessed=False
        )
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

    def propose(self, line_id: int, text: str, source: str, translated: bool = False) -> str:
        """Act on a proposed fix for a line: apply it when another source agrees, otherwise
        offer it to the learner. `source` is "vision" or "explain". `translated`: the fix
        came with a translation of the fixed text, so the page needn't translate again."""
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
            self._call("lineCorrected", {"id": line_id, "text": text, "words": words, "was": line["was"], "translated": translated})
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
        if self._hotkey_error and not message:
            self._status("error", f"{_pretty_hotkey(self.hotkey_spec)} is taken by another program: choose a capture hotkey in Settings.")
            return
        self._status("ready", message or f"Ready. Press {_pretty_hotkey(self.hotkey_spec)} in your game.")

    def _status(self, state: str, message: str) -> None:
        self._call("setStatus", {"state": state, "message": message})

    def _call(self, function: str, *args) -> None:
        payload = ", ".join(json.dumps(a, ensure_ascii=False) for a in args)
        self.window.evaluate_js(f"window.horu && horu.{function}({payload})")


def _game_label(game: str) -> str:
    """P5R for P5R.exe."""
    return game[:-4] if game.lower().endswith(".exe") else game


def _same_line(a: str, b: str) -> bool:
    """The same text, allowing for OCR noise (see _REPEAT_SLACK). Compared again on the
    Japanese alone, since button icons inside a line (Ⅰ・Ⓧ・Ⓨ・Ⅱにセット) read differently
    every time: ・II, ・, 皿."""
    a, b = _repeat_key(a), _repeat_key(b)
    japanese = lambda t: "".join(_JAPANESE_OR_DIGIT.findall(t))
    return _close(a, b) or _close(japanese(a), japanese(b))


def _repeat_key(text: str) -> str:
    """Text as compared for repeats: no spaces, one width, and small kana as full-size ones
    (OCR reads ディ as デイ when the ィ is drawn large)."""
    return unicodedata.normalize("NFKC", corrections.normalize(text)).translate(_FULL_SIZE)


def _close(a: str, b: str) -> bool:
    if a == b:
        return True
    shorter = min(len(a), len(b))
    if shorter < _SLACK_FROM:
        return False
    slack = max(1, shorter // _REPEAT_SLACK)
    return abs(len(a) - len(b)) <= slack and edit_distance(a, b) <= slack


def _pieces_of(text: str, shown: list[str]) -> bool:
    """`text` was shown already, read differently: in pieces that are now read whole (the
    mouse pointer over a line split it), or as a piece of a longer line (the pointer covering
    its end). Short pieces don't count: they turn up inside longer lines by chance."""
    text, shown = _repeat_key(text), [_repeat_key(t) for t in shown]
    if len(text) >= _PIECE_CHARS and any(text in line for line in shown):
        return True
    for count in (2, 3):
        for start in range(len(shown) - count + 1):
            if _same_line(text, "".join(shown[start : start + count])):
                return True
    return False


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
