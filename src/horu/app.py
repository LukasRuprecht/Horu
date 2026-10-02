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

from horu import capture, config, dictionary, hotkey, layout, text_sources
from horu.nlp import analyze

UI_DIR = Path(__file__).parent / "ui"
# Blocks already shown recently are skipped, so persistent text (HUD, menus) and repeated
# hotkey presses on the same dialogue don't flood the history.
_RECENT_LINES = 20
# Blocks centred below this fraction of the frame height are treated as dialogue.
_DIALOGUE_ZONE = 0.6


class Api:
    """Methods JavaScript can call as `window.pywebview.api.<name>(...)`."""

    def __init__(self, app: App) -> None:
        self._app = app

    def lookup(self, word: dict) -> list[dict]:
        return self._app.lookup(word)

    def reanalyze(self, text: str) -> list[dict]:
        return [w.to_dict() for w in analyze(text)]

    def set_on_top(self, on_top: bool) -> None:
        self._app.window.on_top = bool(on_top)

    def clear(self) -> None:
        self._app.recent.clear()


class App:
    def __init__(self, cfg: config.Config, debug: bool = False) -> None:
        self.cfg = cfg
        self.debug = debug
        self.window = None
        self.recent: deque[str] = deque(maxlen=_RECENT_LINES)
        self._source: text_sources.TextSource | None = None
        self._dictionary: dictionary.Dictionary | None = None
        self._requests: queue.Queue[None] = queue.Queue()
        self._ids = itertools.count(1)
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
            background_color="#15171d",
        )
        self.window.events.loaded += self._page_loaded.set
        # webview.start blocks on the main thread; _worker runs on a background thread.
        webview.start(self._worker, debug=self.debug)
        if self._stop_hotkey:
            self._stop_hotkey()

    def lookup(self, word: dict) -> list[dict]:
        return self._dictionary.lookup(word) if self._dictionary else []

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
            lines.append(self._line(block, image.height))
        if lines:
            self._call("addLines", lines)
            self._ready()
        else:
            self._ready("No new Japanese text found.")

    def _line(self, block: layout.TextBlock, frame_height: int) -> dict:
        top, bottom = block.box[1], block.box[3]
        is_dialogue = block.speaker is not None or (top + bottom) / 2 > _DIALOGUE_ZONE * frame_height
        return {
            "id": next(self._ids),
            "time": time.strftime("%H:%M"),
            "speaker": block.speaker,
            "text": block.text,
            "kind": "dialogue" if is_dialogue else "screen",
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
