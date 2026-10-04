"""Score OCR accuracy against evals/ocr_truth.json.

    uv run python evals/eval_ocr.py                 # current pipeline
    uv run python evals/eval_ocr.py --show          # also print each line's read vs truth
    uv run python evals/eval_ocr.py --no-vote       # compare without a pipeline step

For every true line, the closest stretch of OCR text on that screen is found, so a line OCR
merged with its neighbour still counts as read; joining lines is the layout step's job, tested
elsewhere. Reported per screen and overall:

- CER: character error rate, edits needed / true characters (lower is better)
- missed: true lines more than half wrong
- junk: OCR'd Japanese lines that match nothing on the screen
- optional: stylised text and button labels found (not part of CER)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from horu.layout import is_japanese  # noqa: E402
from horu.text_sources.rapidocr_source import RapidOcrSource  # noqa: E402

_SAME = str.maketrans({"·": "・", "･": "・", "〜": "～"})


def normalize(text: str) -> str:
    """Ignore what doesn't change meaning: character width, spacing, Latin letters (OCR
    often attaches ON/OFF graphics or key names to menu items), and dot variants."""
    text = unicodedata.normalize("NFKC", text).translate(_SAME)
    return re.sub(r"[\sA-Za-z]", "", text)


def edit_distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def substring_distance(needle: str, haystack: str) -> int:
    """Edits to turn `needle` into the closest substring of `haystack` (free start and end)."""
    previous = [0] * (len(haystack) + 1)
    for i, cn in enumerate(needle, 1):
        current = [i]
        for j, ch in enumerate(haystack, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (cn != ch)))
        previous = current
    return min(previous)


def best_match(truth: str, reads: list[str]) -> tuple[int, str]:
    if not reads:
        return len(truth), ""
    return min(((substring_distance(truth, r), r) for r in reads), key=lambda x: x[0])


def evaluate(source, screens: list[dict], captures: Path, show: bool) -> dict:
    totals = {"chars": 0, "edits": 0, "missed": 0, "lines": 0, "junk": 0, "optional": 0, "optional_found": 0, "seconds": 0.0}
    rows = []
    for screen in screens:
        path = captures / screen["file"]
        if not path.exists():
            print(f"  skipped {screen['name']}: {path} not found")
            continue
        image = Image.open(path).convert("RGB")
        start = time.perf_counter()
        reads = [normalize(l.text) for l in source.read(image) if is_japanese(l.text)]
        seconds = time.perf_counter() - start

        truths = [normalize(t) for t in screen["lines"]]
        optional = [normalize(t) for t in screen.get("optional", [])]
        edits = missed = 0
        details = []
        for truth in truths:
            distance, read = best_match(truth, reads)
            edits += distance
            missed += distance > len(truth) / 2
            details.append((truth, read, distance))
        found = sum(best_match(o, reads)[0] <= len(o) / 3 for o in optional)
        known = truths + optional
        junk = [r for r in reads if not known or min(edit_distance(r, k) for k in known) > 0.6 * len(r)
                and not any(substring_distance(k, r) <= len(k) / 3 for k in known)]

        chars = sum(len(t) for t in truths)
        rows.append((screen["name"], edits / max(chars, 1), missed, len(truths), len(junk), found, len(optional), seconds))
        for key, value in (("chars", chars), ("edits", edits), ("missed", missed), ("lines", len(truths)),
                           ("junk", len(junk)), ("optional", len(optional)), ("optional_found", found), ("seconds", seconds)):
            totals[key] += value
        if show:
            print(f"== {screen['name']}")
            for truth, read, distance in details:
                mark = "  " if distance == 0 else "!!"
                print(f"   {mark} {truth}  <-  {read}  ({distance})")
            for j in junk:
                print(f"   junk: {j}")

    print(f"\n{'screen':38} {'CER':>6} {'missed':>7} {'junk':>5} {'optional':>9} {'time':>6}")
    for name, cer, missed, lines, junk, found, optional, seconds in rows:
        print(f"{name:38} {cer:6.1%} {missed:>3}/{lines:<3} {junk:>5} {found:>4}/{optional:<4} {seconds:5.2f}s")
    overall = totals["edits"] / max(totals["chars"], 1)
    print(
        f"\n{'OVERALL':38} {overall:6.1%} {totals['missed']:>3}/{totals['lines']:<3} {totals['junk']:>5}"
        f" {totals['optional_found']:>4}/{totals['optional']:<4} {totals['seconds']:5.2f}s"
    )
    return {"cer": overall, **totals}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--captures", type=Path, default=ROOT / "captures")
    parser.add_argument("--truth", type=Path, default=ROOT / "evals" / "ocr_truth.json")
    parser.add_argument("--show", action="store_true", help="print each line's read next to the truth")
    parser.add_argument("--upscale", action="store_true", help="enlarge small text before OCR (experimental)")
    parser.add_argument("--no-vote", action="store_true", help="don't correct uncertain lines by voting")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    screens = json.loads(args.truth.read_text(encoding="utf-8"))["screens"]
    source = RapidOcrSource(upscale=args.upscale, vote=not args.no_vote)
    evaluate(source, screens, args.captures, args.show)


if __name__ == "__main__":
    main()
