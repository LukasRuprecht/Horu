"""Measure what Horu's AI features cost and how well they work, on real screens.

    uv run python evals/eval_ai.py --provider deepseek --model deepseek-flash --out run.json
    uv run python evals/eval_ai.py --compare before.json after.json

Captures go through Horu as they would while playing (with OCR, layout, names, translations,
and AI checks), with the real AI. Data is kept in a temporary folder: nothing touches data/.
This sends real requests, so it costs a little.

Two sets of screens:
- the screens of evals/ground_truth.json, read with the hotkey: the final text of each line
  (after any AI fix) and its speaker are scored against the truth;
- --session globs of saved captures (captures/ keeps them with save_captures on), read as
  auto-capture would: the first with the hotkey, the rest as auto reads. Each glob is one game.

Reported: AI requests (and HTTP attempts, which include the SDK's retries), images sent,
tokens, seconds to each translation, OCR errors left on the truth screens, and every
translation, for comparing runs side by side.
"""

from __future__ import annotations

import argparse
import dataclasses
import glob
import json
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "evals"))

from eval_ocr import normalize  # noqa: E402
from horu import app as horu_app  # noqa: E402
from horu import autocapture, capture, config, corrections, hotkey, prefs, speakers, translations, wordbook  # noqa: E402
from horu.text_sources.vote import edit_distance  # noqa: E402

_lock = threading.Lock()
requests: list[dict] = []
attempts = [0]
in_flight = [0]


def instrument() -> None:
    """Record every chat request the OpenAI SDK sends (Horu's OpenAI-compatible providers)."""
    import httpx2 as httpx
    from openai.resources.chat.completions import Completions

    create = Completions.create

    def recorded(self, *args, **kwargs):
        messages = kwargs.get("messages", [])
        content = messages[-1]["content"] if messages else ""
        parts = content if isinstance(content, list) else [{"type": "text", "text": content}]
        text = " ".join(p.get("text", "") for p in parts if p.get("type") == "text")
        entry = {
            "images": sum(p.get("type") == "image_url" for p in parts),
            "prompt": text[:160],
            "kind": _kind(text, any(p.get("type") == "image_url" for p in parts)),
            "started": time.monotonic(),
        }
        if kwargs.get("stream"):
            kwargs["stream_options"] = {"include_usage": True}
        with _lock:
            requests.append(entry)
            in_flight[0] += 1
        try:
            result = create(self, *args, **kwargs)
        except Exception as e:
            entry["error"] = type(e).__name__
            with _lock:
                in_flight[0] -= 1
            raise
        if not kwargs.get("stream"):
            _usage(entry, result.usage)
            with _lock:
                in_flight[0] -= 1
            return result
        return _Stream(result, entry)

    Completions.create = recorded

    send = httpx.Client.send

    def counted(self, request, *args, **kwargs):
        if request.url.path.endswith("/chat/completions"):
            with _lock:
                attempts[0] += 1
        return send(self, request, *args, **kwargs)

    httpx.Client.send = counted


class _Stream:
    def __init__(self, stream, entry: dict) -> None:
        self._stream, self._entry = stream, entry

    def __iter__(self):
        try:
            for chunk in self._stream:
                if chunk.usage:
                    _usage(self._entry, chunk.usage)
                yield chunk
        finally:
            self._entry["seconds"] = round(time.monotonic() - self._entry["started"], 2)
            with _lock:
                in_flight[0] -= 1

    def close(self) -> None:
        self._stream.close()


def _usage(entry: dict, usage) -> None:
    entry["seconds"] = round(time.monotonic() - entry["started"], 2)
    if usage:
        entry["in"] = usage.prompt_tokens
        entry["out"] = usage.completion_tokens


def _kind(text: str, image: bool) -> str:
    if "speaker label" in text:
        return "name translation"
    if "Transcribe" in text:
        return "image check"
    if "Translate" in text or "translat" in text:
        return "translation" + (" + check" if image else "")
    return "other"


def settle(app) -> None:
    """Wait until no AI work is left: no requests in flight and no AI threads, for a while."""
    quiet = 0
    deadline = time.monotonic() + 300
    while quiet < 6 and time.monotonic() < deadline:
        busy = in_flight[0] or any(
            t.name in ("horu-ai", "horu-check", "horu-speaker", "horu-auto-cards") for t in threading.enumerate()
        ) or getattr(app, "_ai_busy", lambda: False)()
        quiet = 0 if busy else quiet + 1
        time.sleep(0.25)


def make_app(provider: str, model: str, tmp: Path):
    hotkey.listen = lambda spec, cb: (lambda: None)
    app = horu_app.App(dataclasses.replace(config.load(), save_captures=False))
    app.prefs = prefs.Prefs(tmp / "prefs.json")
    app.prefs.values.update(
        aiProvider=provider, aiModel=model, translationModel="", autoTranslate=True, aiChecks=True,
        applyAiReadings=True, autoCards=False,
    )
    app.speakers = speakers.SpeakerBook(tmp / "speakers.json")
    app.corrections = corrections.Corrections(tmp / "corrections.json")
    app.boxes = autocapture.DialogueBoxes(tmp / "boxes.json")
    app.words = wordbook.Wordbook(tmp / "words.db")
    app.translations = translations.TranslationMemory(tmp / "translations.db")
    ocr: dict[int, tuple[str, str | None]] = {}  # line id -> (text, speaker) as first shown

    def call(function: str, *args) -> None:
        if function == "addCapture":
            for line in args[0]["lines"]:
                ocr[line["id"]] = (line["text"], line["speaker"]["text"] if line["speaker"] else None)

    app._call = call
    app._ocr = ocr
    timings: dict[int, dict[str, float]] = {}
    started = app._started

    def timed(line_id: int, event: str) -> None:
        if line_id in started:
            timings.setdefault(line_id, {})[event] = round(time.monotonic() - started[line_id], 2)

    app._time = timed
    app._load()
    if app._ai.provider is None:
        sys.exit(f"AI not set up: {app._ai.error}")
    return app, timings


def capture_with(app, image: Image.Image, game: str, auto: bool) -> list[int]:
    """Read one capture; returns the ids of the lines it added."""
    before = set(app.lines)
    if auto:
        app._capture(horu_app.AutoRead(image, game))
    else:
        capture.grab = lambda settings: image
        capture.focused_window_is_own = lambda: False
        capture.focused_process_name = lambda: game
        capture.foreground_window = lambda: None
        app._capture(None)
    settle(app)
    return [i for i in app.lines if i not in before]


def run(args) -> dict:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    instrument()
    tmp = Path(tempfile.mkdtemp())
    app, timings = make_app(args.provider, args.model, tmp)
    started = time.monotonic()
    ocr = app._ocr
    note = lambda ids: None

    truth_lines = []
    screens = json.loads((ROOT / "evals" / "ground_truth.json").read_text(encoding="utf-8"))["screens"]
    for screen in screens:
        path = args.captures / screen["file"]
        if not path.exists():
            continue
        # Captures already read stay "recent": each screen starts afresh, as if read later.
        app.recent.clear()
        ids = capture_with(app, Image.open(path).convert("RGB"), "truth-screens", auto=False)
        note(ids)
        truth_lines.append((screen, ids))
        print(f"  {screen['name']}: {len(ids)} lines", flush=True)

    for session in args.session:
        game, _, patterns = session.rpartition("=")
        files = sorted(f for pattern in patterns.split(",") for f in glob.glob(pattern))
        for k, path in enumerate(files):
            note(capture_with(app, Image.open(path).convert("RGB"), game or "session", auto=k > 0))
        print(f"  session {session}: {len(files)} captures", flush=True)

    settle(app)
    seconds = time.monotonic() - started

    # Score the truth screens: each true block against the closest line Horu showed.
    scored = []
    for screen, ids in truth_lines:
        shown = [app.lines[i] for i in ids if not app.lines[i]["repeat"]]
        for block in screen["blocks"]:
            want = normalize("".join(block["lines"]))
            if not shown or not want:
                continue
            line = min(shown, key=lambda l: edit_distance(want, normalize(ocr[l["id"]][0])))
            before = edit_distance(want, normalize(ocr[line["id"]][0]))
            if before > len(want) / 2:
                continue  # not found at all: a layout problem, not this eval's
            speaker_want = normalize(block.get("speaker"))
            speaker_ocr = normalize(ocr[line["id"]][1])
            speaker_final = normalize(line["speaker"]["text"] if line["speaker"] else None)
            scored.append({
                "screen": screen["name"], "kind": block["kind"], "truth": "".join(block["lines"]),
                "ocr": ocr[line["id"]][0], "final": line["text"], "chars": len(want),
                "edits_before": before, "edits_after": edit_distance(want, normalize(line["text"])),
                "speaker_truth": block.get("speaker"), "speaker_ok_before": speaker_want == speaker_ocr,
                "speaker_ok_after": speaker_want == speaker_final,
            })

    lines = []
    for i, line in app.lines.items():
        if line["repeat"]:
            continue
        speaker = line["speaker"]
        lines.append({
            "id": i, "game": line["game"], "kind": line["kind"], "ocr": ocr.get(i, (line["text"],))[0],
            "text": line["text"], "speaker_ocr": ocr.get(i, (None, None))[1],
            "speaker": speaker["text"] if speaker else None,
            "speaker_english": app.speakers.english(line["game"], speaker["text"]) if speaker else None,
            "translation": line.get("translation"), "uncertain": line["uncertain"], "timings": timings.get(i, {}),
        })
    return {
        "provider": args.provider, "model": args.model, "seconds": round(seconds, 1),
        "requests": requests, "attempts": attempts[0], "truth": scored, "lines": lines,
    }


def summary(result: dict) -> dict:
    reqs = result["requests"]
    kinds: dict[str, int] = {}
    for r in reqs:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    truth = result["truth"]
    dialogue = [t for t in truth if t["kind"] == "dialogue"]
    translated = [l["timings"]["translated"] for l in result["lines"] if "translated" in l["timings"]]
    chars = sum(t["chars"] for t in truth) or 1
    return {
        "lines shown": len(result["lines"]),
        "AI requests": len(reqs),
        "HTTP attempts (with retries)": result["attempts"],
        "failed requests": sum("error" in r for r in reqs),
        "images sent": sum(r["images"] for r in reqs),
        "input tokens": sum(r.get("in", 0) for r in reqs),
        "output tokens": sum(r.get("out", 0) for r in reqs),
        "requests by kind": kinds,
        "lines translated": f"{sum(1 for l in result['lines'] if l['translation'])}/{sum(1 for l in result['lines'] if l['kind'] == 'dialogue')} dialogue",
        "seconds to translation (median)": round(statistics.median(translated), 2) if translated else None,
        "truth: CER before AI": f"{sum(t['edits_before'] for t in truth) / chars:.2%}",
        "truth: CER after AI": f"{sum(t['edits_after'] for t in truth) / chars:.2%}",
        "truth: dialogue lines exactly right": f"{sum(t['edits_after'] == 0 for t in dialogue)}/{len(dialogue)} (OCR alone {sum(t['edits_before'] == 0 for t in dialogue)})",
        "truth: lines made worse": sum(t["edits_after"] > t["edits_before"] for t in truth),
        "truth: speakers right": f"{sum(t['speaker_ok_after'] for t in truth)}/{len(truth)} (OCR alone {sum(t['speaker_ok_before'] for t in truth)})",
    }


def compare(a: dict, b: dict) -> None:
    sa, sb = summary(a), summary(b)
    print(f"{'':36} {'A':>24} {'B':>24}")
    for key in sa:
        va, vb = sa[key], sb[key]
        if isinstance(va, dict):
            for kind in sorted(set(va) | set(vb)):
                print(f"  {kind:34} {va.get(kind, 0):>24} {vb.get(kind, 0):>24}")
        else:
            print(f"{key:36} {str(va):>24} {str(vb):>24}")
    print("\nTruth lines that differ:")
    for ta, tb in zip(a["truth"], b["truth"]):
        if ta["final"] != tb["final"]:
            print(f"  truth {ta['truth']}\n    A {ta['final']}\n    B {tb['final']}")
    print("\nTranslations (by OCR text):")
    by_text = {(l["game"], l["ocr"]): l for l in b["lines"]}
    for la in a["lines"]:
        lb = by_text.get((la["game"], la["ocr"]))
        if la["kind"] != "dialogue" or lb is None:
            continue
        speaker = f"{la['speaker']} / {lb['speaker']}" if la["speaker"] != lb["speaker"] else la["speaker"]
        print(f"  {la['ocr']}  [{speaker}] ({la['speaker_english']} / {lb['speaker_english']})")
        if la["text"] != lb["text"]:
            print(f"    text A {la['text']}\n    text B {lb['text']}")
        print(f"    A {la['translation']}\n    B {lb['translation']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    default_captures = ROOT / "groundTruths" / "captures"
    if not default_captures.exists():
        default_captures = ROOT.parent / "groundTruths" / "captures"
    parser.add_argument("--provider", default="deepseek", help="a preset in Horu's AI settings, e.g. gemini or deepseek")
    parser.add_argument("--model", default="")
    parser.add_argument("--captures", type=Path, default=default_captures, help="the truth screens' folder")
    parser.add_argument("--session", action="append", default=[], help="GAME=GLOB[,GLOB]: saved captures of one game, read as auto-capture")
    parser.add_argument("--out", type=Path, help="save the full result as JSON")
    parser.add_argument("--compare", nargs=2, type=Path, metavar=("A", "B"), help="compare two saved results")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.compare:
        compare(*(json.loads(p.read_text(encoding="utf-8")) for p in args.compare))
        return
    result = run(args)
    if args.out:
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    for key, value in summary(result).items():
        print(f"{key:36} {value}")


if __name__ == "__main__":
    main()
