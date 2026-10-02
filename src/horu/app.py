"""The Horu desktop app: a pywebview window that shows captured lines word by word."""

from __future__ import annotations

import itertools
import json
import queue
import threading
import time
import traceback
from collections import deque
from pathlib import Path

from horu import capture, config, dictionary, hotkey, layout, llm, prefs, text_sources
from horu.nlp import analyze
from horu.tutor import Line, Tutor

UI_DIR = Path(__file__).parent / "ui"
# Blocks already shown recently are skipped, so persistent text (HUD, menus) and repeated
# hotkey presses on the same dialogue don't flood the history.
_RECENT_LINES = 20
# A block is main text (shown large) if it has a speaker, sits in the lower part of the
# screen where dialogue boxes live, or is long; HUD and menu labels are short.
_DIALOGUE_ZONE = 0.6
_MAIN_TEXT_CHARS = 12
# Earlier dialogue lines sent along with AI requests, so pronouns and names make sense.
_CONTEXT_LINES = 4
# Streamed AI text is pushed to the page at most this often.
_FLUSH_SECONDS = 0.05


class Api:
    """Methods JavaScript can call as `window.pywebview.api.<name>(...)`."""

    def __init__(self, app: App) -> None:
        self._app = app

    def lookup(self, word: dict) -> list[dict]:
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
    def explain_word(self, line_id: int, index: int, session: int) -> None:
        self._app.explain_word(line_id, index, session)

    def explain_line(self, line_id: int, session: int) -> None:
        self._app.explain_line(line_id, session)

    def ask(self, session: int, question: str) -> None:
        self._app.ask(session, question)

    def get_prefs(self) -> dict:
        return self._app.prefs.values

    def set_pref(self, key: str, value) -> None:
        self._app.prefs.set(key, value)
        if key == "onTop":
            self._app.window.on_top = bool(value)

    def clear(self) -> None:
        self._app.recent.clear()
        self._app.lines.clear()


class App:
    def __init__(self, cfg: config.Config, debug: bool = False) -> None:
        self.cfg = cfg
        self.debug = debug
        self.window = None
        self.prefs = prefs.Prefs()
        self.recent: deque[str] = deque(maxlen=_RECENT_LINES)
        self.lines: dict[int, dict] = {}  # every line shown, in order, by id
        self._provider: llm.Provider | None = None
        self._ai_error: str | None = None
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

    def lookup(self, word: dict) -> list[dict]:
        return self._dictionary.lookup(word) if self._dictionary else []

    def update_line(self, line_id: int, text: str) -> list[dict]:
        words = [w.to_dict() for w in analyze(text)]
        if line_id in self.lines:
            self.lines[line_id].update(text=text, words=words)
        return words

    # --- AI ----------------------------------------------------------------------------

    def ai_info(self) -> dict:
        return {
            "enabled": self._provider is not None,
            "label": self._provider.label if self._provider else None,
            "error": self._ai_error,
        }

    def translate(self, line_id: int) -> None:
        if self._provider and line_id in self.lines:
            chunks = self._tutor().translate(self._line_context(line_id))
            self._stream({"target": "translation", "lineId": line_id}, chunks)

    def explain_word(self, line_id: int, index: int, session: int) -> None:
        if self._provider and line_id in self.lines:
            word = self.lines[line_id]["words"][index]
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
        return Tutor(self._provider, self.prefs.values["learnerLevel"])

    def _start_tutor_session(self, session: int, chat: llm.Chat, chunks) -> None:
        # The tutor panel shows one conversation at a time; stop the previous one.
        if self._tutor_chat:
            self._tutor_chat[1].cancel()
        self._tutor_chat = (session, chat)
        self._stream({"target": "tutor", "session": session}, chunks)

    def _line_context(self, line_id: int) -> Line:
        line = self.lines[line_id]
        earlier = [l for i, l in self.lines.items() if i < line_id and l["kind"] == "dialogue"]
        previous = [Line(l["text"], l["speaker"]).quoted() for l in earlier[-_CONTEXT_LINES:]]
        return Line(line["text"], line["speaker"], previous)

    def _stream(self, meta: dict, chunks) -> None:
        """Push an AI reply to the page as it arrives, on a background thread."""

        def run() -> None:
            buffer: list[str] = []
            last_flush = time.monotonic()
            try:
                for chunk in chunks:
                    buffer.append(chunk)
                    if time.monotonic() - last_flush >= _FLUSH_SECONDS:
                        self._call("aiChunk", {**meta, "text": "".join(buffer)})
                        buffer.clear()
                        last_flush = time.monotonic()
                if buffer:
                    self._call("aiChunk", {**meta, "text": "".join(buffer)})
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
            self._status("loading", f"Loading OCR ({self.cfg.ocr_engine})...")
            self._source = text_sources.create(self.cfg.ocr_engine)
            self._stop_hotkey = hotkey.listen(self.cfg.capture_hotkey, lambda: self._requests.put(None))
            ai = self.cfg.ai
            try:
                self._provider = llm.create(ai.provider, ai.model, ai.base_url, ai.api_key_env)
            except llm.AIError as e:
                self._ai_error = str(e)  # AI is optional: report it and carry on
            self._call("setAI", self.ai_info())
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
        blocks = layout.group_blocks(self._source.read(image))

        lines = []
        for block in blocks:
            if block.text in self.recent:
                continue
            self.recent.append(block.text)
            line = self._line(block, image.height)
            self.lines[line["id"]] = line
            lines.append(line)
        if lines:
            self._call("addCapture", {"id": next(self._capture_ids), "time": time.strftime("%H:%M:%S"), "lines": lines})
            self._ready()
            if self.prefs.values["autoTranslate"]:
                for line in lines:
                    if line["kind"] == "dialogue":
                        self.translate(line["id"])
        else:
            self._ready("No new Japanese text found.")

    def _line(self, block: layout.TextBlock, frame_height: int) -> dict:
        top, bottom = block.box[1], block.box[3]
        is_main = (
            block.speaker is not None
            or (top + bottom) / 2 > _DIALOGUE_ZONE * frame_height
            or len(block.text) >= _MAIN_TEXT_CHARS
        )
        return {
            "id": next(self._ids),
            "speaker": block.speaker,
            "text": block.text,
            "kind": "dialogue" if is_main else "screen",
            "words": [w.to_dict() for w in analyze(block.text)],
        }

    # --- talking to the page ---------------------------------------------------------

    def _ready(self, message: str | None = None) -> None:
        self._status("ready", message or f"Ready. Press {_pretty_hotkey(self.cfg.capture_hotkey)} in your game.")

    def _status(self, state: str, message: str) -> None:
        self._call("setStatus", {"state": state, "message": message})

    def _call(self, function: str, *args) -> None:
        payload = ", ".join(json.dumps(a, ensure_ascii=False) for a in args)
        self.window.evaluate_js(f"window.horu && horu.{function}({payload})")


def _pretty_hotkey(spec: str) -> str:
    return "+".join(part.strip("<>").capitalize() for part in spec.split("+"))
