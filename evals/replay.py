"""Score auto-capture on a recording (Settings → Help → Record auto-capture).

    uv run python evals/replay.py debug/recordings/<folder>
    uv run python evals/replay.py <folder> --ai                    # an AI model says what is story text
    uv run python evals/replay.py <folder> --try "_STILL_FRAMES=3" --try "_PATIENCE=4,_CHANGED=0.2"
    uv run python evals/replay.py <folder> --watcher mymodule:MyWatcher

What was really on screen comes from reading the recording's whole-window pictures (about
one a second while the picture changed) with Horu's normal pipeline; the result is kept in
the folder (truth.json), so only the first run is slow. Each piece of text is followed from
when it appears to when it goes ("a line"), joining a line that types out into one. Story
lines are the ones auto-capture should read: by default what today's rules call dialogue,
with --ai what the AI set up in Horu calls story text (kept in labels.json).

Then the watcher is run again over the recorded frames, as it was live and with each rule
to try, and every read it would set off is checked against the lines on screen then:

  read      it showed a story line, complete, for the first time
  partial   ...a line still typing out (Horu updates it when it's read again in full)
  wasted    nothing new: the read cost a second of CPU and showed nothing

and every story line is: read (how long after it was complete), read only in part, or missed.
Lines on screen for under a second aren't counted as missed. Hotkey presses in the
recording are listed: each is a moment the player wanted a read auto-capture hadn't given.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from horu import ai_setup, autocapture, config, prefs  # noqa: E402
from horu.layout import group_blocks  # noqa: E402
from horu.text_sources.vote import edit_distance  # noqa: E402

BRIEF = 1.0  # seconds: a line on screen for less can't be expected to be read
GONE = 3.0  # seconds without a line before it counts as having left the screen
STORY = {"dialogue", "thought", "choice", "narration", "message", "objective", "system"}
CATEGORIES = (
    "dialogue (a character speaking, subtitles), thought (the player character's inner thoughts), "
    "choice (a reply the player can pick), narration, message (a text, chat or letter being read), "
    "objective (the current goal), system (a full-sentence prompt, tutorial or 'you received X' notice), "
    "description (prose about an item or menu entry), sign (writing in the 3D world), menu (a menu entry or label), "
    "title (a location or chapter name), name (a speaker's name tag alone), hud (date, money, counters), "
    "legend (button hints), junk (a misread icon or texture)"
)


def norm(text: str) -> str:
    return text.replace(" ", "").replace("　", "")


def same(a: str, b: str) -> bool:
    return edit_distance(a, b) <= max(1, round(0.2 * max(len(a), len(b))))


def grows_into(a: str, b: str) -> bool:
    """`a` is `b` still typing out: b starts with (nearly) a."""
    return 2 <= len(a) < len(b) and edit_distance(a, b[: len(a)]) <= max(0, round(0.15 * len(a)))


def near(a, b) -> bool:
    """Boxes (as fractions of the window) in the same band of the screen."""
    v = min(a[3], b[3]) - max(a[1], b[1])
    return v > 0.4 * min(a[3] - a[1], b[3] - b[1]) and min(a[2], b[2]) > max(a[0], b[0])


@dataclass
class Line:
    text: str
    box: tuple
    first: float  # when it appeared
    ready: float  # when it reached its final text
    rule_dialogue: bool
    looks: set = field(default_factory=set)  # the looks it was on screen for
    last: float = 0.0
    category: str | None = None

    @property
    def shown_for(self) -> float:
        return self.last - self.first


def load_log(folder: Path) -> tuple[list[dict], list[dict]]:
    rows = [json.loads(line) for line in (folder / "log.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if "look" in r], [r for r in rows if "event" in r]


def read_fulls(folder: Path, looks: list[dict]) -> dict[int, list[dict]]:
    """What Horu's pipeline reads in each whole-window picture: look -> blocks."""
    path = folder / "truth.json"
    truth = {int(k): v for k, v in json.loads(path.read_text(encoding="utf-8")).items()} if path.exists() else {}
    todo = [r["full"] for r in looks if r["full"] is not None and r["full"] not in truth]
    if todo:
        from horu.text_sources.rapidocr_source import RapidOcrSource

        source = RapidOcrSource()
        for i, n in enumerate(todo):
            image = Image.open(folder / "full" / f"{n:06d}.jpg").convert("RGB")
            w, h = image.size
            truth[n] = [
                {"text": norm(b.text), "box": [b.box[0] / w, b.box[1] / h, b.box[2] / w, b.box[3] / h], "dialogue": b.kind == "dialogue",
                 "speaker": b.speaker}
                for b in group_blocks(source.read(image), image)
            ]
            if i % 50 == 0:
                print(f"  reading whole-window pictures: {i}/{len(todo)}", flush=True)
                path.write_text(json.dumps(truth, ensure_ascii=False), encoding="utf-8")
        path.write_text(json.dumps(truth, ensure_ascii=False), encoding="utf-8")
    return truth


def picture_for(looks: list[dict]) -> dict[int, int | None]:
    """The whole-window picture that shows what each look saw: one of the same frame if there
    is one, else the nearest in time."""
    fulls = [(r["look"], r["t"], r["frame"]) for r in looks if r["full"] is not None]
    out, k = {}, -1
    for r in looks:
        while k + 1 < len(fulls) and fulls[k + 1][0] <= r["look"]:
            k += 1
        before = fulls[k] if k >= 0 else None
        after = fulls[k + 1] if k + 1 < len(fulls) else None
        if before and before[2] == r["frame"]:
            out[r["look"]] = before[0]
        elif after and after[1] - r["t"] <= 1.0 and (after[2] == r["frame"] or before is None or after[1] - r["t"] < r["t"] - before[1]):
            out[r["look"]] = after[0]
        else:
            out[r["look"]] = before[0] if before else None
    return out


def follow_lines(looks: list[dict], truth: dict[int, list[dict]]) -> list[Line]:
    shows = picture_for(looks)
    lines: list[Line] = []
    for r in looks:
        blocks = truth.get(shows[r["look"]], []) if shows[r["look"]] is not None else []
        t = r["t"]
        for b in blocks:
            text, box = b["text"], b["box"]
            if len(text) < 2:
                continue
            live = [l for l in lines if t - l.last <= GONE]
            match = next((l for l in live if near(l.box, box) and same(l.text, text)), None)
            if match is None:
                grown = next((l for l in live if near(l.box, box) and grows_into(l.text, text)), None)
                if grown is not None:  # typed out further
                    grown.text, grown.ready, grown.box, match = text, t, box, grown
                    grown.rule_dialogue = grown.rule_dialogue or b["dialogue"]
                else:
                    match = next((l for l in live if near(l.box, box) and grows_into(text, l.text)), None)  # a frame read short
            if match is None:
                match = next((l for l in lines if len(text) >= 4 and same(l.text, text)), None)  # back again: Horu shows it once
            if match is None:
                match = Line(text, tuple(box), t, t, b["dialogue"])
                lines.append(match)
            match.looks.add(r["look"])
            match.last = t
    return lines


def label_with_ai(folder: Path, lines: list[Line]) -> None:
    path = folder / "labels.json"
    labels = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    provider = ai_setup.resolve(prefs.Prefs().values, config.load().ai).provider
    if provider is None:
        raise SystemExit("--ai needs an AI provider set up in Horu (Settings → AI).")
    todo = [l for l in lines if l.text not in labels]
    for start in range(0, len(todo), 30):
        chunk = todo[start : start + 30]
        rows = [{"n": i, "text": l.text, "x%": round(100 * l.box[0]), "y%": round(100 * l.box[1]), "height%": round(100 * (l.box[3] - l.box[1]), 1)}
                for i, l in enumerate(chunk)]
        prompt = (
            "These pieces of text were read by OCR (it can misread characters) off the screen of one Japanese video game, "
            "in the order they appeared. Positions are percentages of the screen. Give each exactly one category: "
            + CATEGORIES + '.\nAnswer with JSON only: {"blocks": [{"n": <number>, "category": "<category>"}, ...]}\n\n'
            + json.dumps(rows, ensure_ascii=False)
        )
        reply = "".join(provider.chat("You label game screen text for a language-learning tool.", effort="none").send(prompt))
        try:
            answer = json.loads(reply[reply.index("{") : reply.rindex("}") + 1])
            for entry in answer["blocks"]:
                labels[chunk[int(entry["n"])].text] = entry["category"]
        except (ValueError, KeyError, IndexError):
            print("  the AI's answer couldn't be read for", len(chunk), "lines: they fall back to today's rules")
        path.write_text(json.dumps(labels, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  labelled {min(start + 30, len(todo))}/{len(todo)}", flush=True)
    for l in lines:
        l.category = labels.get(l.text)


def is_story(line: Line) -> bool:
    return line.category in STORY if line.category else line.rule_dialogue


def replay(folder: Path, looks: list[dict], make_watcher) -> list[int]:
    """The looks at which the watcher sets off a read, run again over the recorded frames."""
    fired, watcher, shown, image = [], None, None, None
    for r in looks:
        if r["fresh"] or watcher is None:
            watcher = make_watcher()
        if r["frame"] != shown:
            image, shown = Image.open(folder / "frames" / f"{r['frame']:06d}.jpg").convert("RGB"), r["frame"]
        if watcher.update(image, [tuple(box) for box in r["regions"]] or None):
            fired.append(r["look"])
    return fired


def score(name: str, fired: list[int], looks: list[dict], story: list[Line]) -> dict:
    time_of = {r["look"]: r["t"] for r in looks}
    got: dict[int, float] = {}  # line index -> delay
    partly: set[int] = set()
    kinds = {"read": 0, "partial": 0, "wasted": 0}
    for look in fired:
        t = time_of[look]
        on_screen = [i for i, l in enumerate(story) if look in l.looks]
        complete = [i for i in on_screen if i not in got and t >= story[i].ready - 0.3]
        typing = [i for i in on_screen if i not in got and i not in partly and t < story[i].ready - 0.3]
        for i in complete:
            got[i] = t - story[i].ready
        partly.update(typing)
        kinds["read" if complete else "partial" if typing else "wasted"] += 1
    counted = [i for i, l in enumerate(story) if l.shown_for >= BRIEF or i in got]
    delays = sorted(got[i] for i in counted if i in got)
    minutes = max((looks[-1]["t"] - looks[0]["t"]) / 60, 1e-6)
    return {
        "name": name, "reads": len(fired), "per_min": len(fired) / minutes, **kinds,
        "lines": len(counted), "got": len(delays), "part": sum(i in partly and i not in got for i in counted),
        "missed": [story[i] for i in counted if i not in got and i not in partly],
        "delay": float(np.median(delays)) if delays else 0.0, "delay90": float(np.percentile(delays, 90)) if delays else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--ai", action="store_true", help="have the AI set up in Horu say which lines are story text")
    parser.add_argument("--try", dest="tries", action="append", default=[], metavar="NAME=VALUE,...", help="autocapture settings to try")
    parser.add_argument("--watcher", action="append", default=[], metavar="MODULE:CLASS", help="another watcher class to try (same interface as TextWatcher)")
    parser.add_argument("--missed", type=int, default=15, help="how many missed lines to list")
    args = parser.parse_args()
    folder = args.folder
    looks, events = load_log(folder)
    if not looks:
        raise SystemExit("No looks in this recording: auto-capture never watched a game while it ran.")
    start = next((e for e in events if e["event"] == "start"), {})
    minutes = (looks[-1]["t"] - looks[0]["t"]) / 60
    print(f"{start.get('game', '?')}: {minutes:.1f} min, {len(looks)} looks, {len({r['frame'] for r in looks})} distinct frames, "
          f"{sum(r['full'] is not None for r in looks)} whole-window pictures")

    truth = read_fulls(folder, looks)
    lines = follow_lines(looks, truth)
    if args.ai:
        label_with_ai(folder, lines)
    story = [l for l in lines if is_story(l)]
    print(f"text on screen: {len(lines)} pieces, {len(story)} story lines ({'the AI says' if args.ai else "today's rules say"} so), "
          f"{sum(l.shown_for < BRIEF for l in story)} of them on screen under {BRIEF:.0f} s")

    from horu.text_sources.rapidocr_source import text_finder

    find = text_finder(autocapture.MAP_WIDTH)
    results = [score("as recorded (live)", [r["look"] for r in looks if r["fired"]], looks, story)]
    current = replay(folder, looks, lambda: autocapture.TextWatcher(find))
    results.append(score("today's rule, replayed", current, looks, story))
    live = {r["look"] for r in looks if r["fired"]}
    agree = sum(any(abs(a - b) <= 2 for b in live) for a in current)
    print(f"replay check: {agree}/{len(current)} replayed reads fall within 2 looks of a live one ({len(live)} live)")
    for spec in args.tries:
        saved = {}
        for pair in spec.split(","):
            key, value = pair.split("=")
            if key == "MAP_WIDTH":
                raise SystemExit("MAP_WIDTH can't be tried on a recording: its frames were kept at the width it was made with.")
            saved[key] = getattr(autocapture, key)
            setattr(autocapture, key, type(saved[key])(float(value)))
        results.append(score(spec, replay(folder, looks, lambda: autocapture.TextWatcher(find)), looks, story))
        for key, value in saved.items():
            setattr(autocapture, key, value)
    for spec in args.watcher:
        module, cls = spec.split(":")
        make = getattr(importlib.import_module(module), cls)
        results.append(score(spec, replay(folder, looks, lambda: make(find)), looks, story))

    print(f"\n{'':<28}{'reads':>6}{'/min':>6}{'read':>6}{'partial':>8}{'wasted':>8}  |{'lines':>6}{'read':>6}{'in part':>8}{'missed':>7}{'delay':>7}{'p90':>6}")
    for r in results:
        share = lambda n, of: f"{100 * n / of:.0f}%" if of else "-"
        print(f"{r['name'][:27]:<28}{r['reads']:>6}{r['per_min']:>6.1f}{share(r['read'], r['reads']):>6}{share(r['partial'], r['reads']):>8}"
              f"{share(r['wasted'], r['reads']):>8}  |{r['lines']:>6}{share(r['got'], r['lines']):>6}{share(r['part'], r['lines']):>8}"
              f"{share(len(r['missed']), r['lines']):>7}{r['delay']:>6.1f}s{r['delay90']:>5.1f}s")
    for r in results[1:2] + results[2:]:
        if r["missed"]:
            print(f"\nmissed by {r['name']} ({len(r['missed'])}):")
            for l in r["missed"][: args.missed]:
                print(f"  {l.first:7.1f}s  on screen {l.shown_for:4.1f}s  {l.text[:50]}")
    pressed = [e for e in events if e["event"] == "read" and not e.get("auto")]
    if pressed:
        print(f"\nhotkey presses while recording ({len(pressed)}): what the player asked for by hand")
        for e in pressed[: args.missed]:
            new = [l["text"][:40] for l in e["lines"] if not l["repeat"]]
            print(f"  {e['t']:7.1f}s  " + (" / ".join(new) if new else "(nothing new)"))


if __name__ == "__main__":
    main()
