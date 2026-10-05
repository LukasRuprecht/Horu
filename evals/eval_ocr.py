"""Score Horu's reading of real screens against evals/ground_truth.json.

    uv run python evals/eval_ocr.py                    # current pipeline
    uv run python evals/eval_ocr.py --show             # also print each block vs the truth
    uv run python evals/eval_ocr.py --no-vote          # compare without a pipeline step
    uv run python evals/eval_ocr.py --captures DIR     # where the screenshots are

Two things are scored per screen:

OCR (was the text read correctly?)
- CER: character error rate over every true line and speaker, edits needed / true characters.
  Each true line is matched to the closest stretch of OCR text, so a line merged with its
  neighbour still counts as read; joining is scored separately below.
- missed: true lines more than half wrong
- junk: OCR'd Japanese lines that match nothing on the screen

Layout (was it organised correctly?), for each true block, using Horu's closest block:
- found: a block with nearly the same text exists
- grouped: that block has the same lines (wrapped lines joined, separate items kept apart)
- speaker: same speaker (or both none), allowing one misread character
- kind: same dialogue/other classification

Width (？ vs ?), spacing, and Latin letters are ignored throughout. 'optional' text (stylised
art text, button labels) is reported as found or not, but not scored.
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

from horu.layout import group_blocks, is_japanese  # noqa: E402
from horu.text_sources.rapidocr_source import RapidOcrSource  # noqa: E402
from horu.text_sources.vote import edit_distance  # noqa: E402

_SAME = str.maketrans({"·": "・", "･": "・", "〜": "～"})


def normalize(text: str) -> str:
    """Ignore what doesn't change meaning: character width, spacing, Latin letters (OCR
    often attaches ON/OFF graphics or key names to menu items), and dot variants."""
    text = unicodedata.normalize("NFKC", text or "").translate(_SAME)
    return re.sub(r"[\sA-Za-z]", "", text)


def substring_distance(needle: str, haystack: str) -> int:
    """Edits to turn `needle` into the closest substring of `haystack` (free start and end)."""
    previous = [0] * (len(haystack) + 1)
    for i, cn in enumerate(needle, 1):
        current = [i]
        for j, ch in enumerate(haystack, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (cn != ch)))
        previous = current
    return min(previous)


def closest(needle: str, texts: list[str]) -> int:
    return min((substring_distance(needle, t) for t in texts), default=len(needle))


def score_screen(screen: dict, image: Image.Image, source) -> dict:
    start = time.perf_counter()
    lines = source.read(image)
    blocks = group_blocks(lines, image)
    seconds = time.perf_counter() - start

    reads = [normalize(l.text) for l in lines if is_japanese(l.text)]
    truth_texts = [normalize(t) for b in screen["blocks"] for t in [*b["lines"], b.get("speaker") or ""] if t]
    optional = [normalize(t) for t in screen.get("optional", [])]

    # OCR
    edits = sum(closest(t, reads) for t in truth_texts)
    missed = sum(closest(t, reads) > len(t) / 2 for t in truth_texts)
    known = truth_texts + optional
    junk = [r for r in reads if not any(substring_distance(k, r) <= len(k) / 3 or edit_distance(r, k) <= 0.6 * len(r) for k in known)]
    optional_found = sum(closest(o, reads) <= len(o) / 3 for o in optional)

    # Layout
    found = grouped = speaker_ok = kind_ok = 0
    details = []
    for truth in screen["blocks"]:
        truth_text = normalize("".join(truth["lines"]))
        best, distance = None, None
        for block in blocks:
            d = edit_distance(truth_text, normalize(block.text))
            if distance is None or d < distance:
                best, distance = block, d
        ok = best is not None and distance <= 0.3 * len(truth_text)
        checks = {"found": ok}
        if ok:
            checks["grouped"] = len(best.lines) == len(truth["lines"])
            want, got = normalize(truth.get("speaker")), normalize(best.speaker)
            checks["speaker"] = want == got or (want and got and edit_distance(want, got) <= 1)
            checks["kind"] = best.kind == truth["kind"]
        found += ok
        grouped += checks.get("grouped", False)
        speaker_ok += bool(checks.get("speaker", False))
        kind_ok += checks.get("kind", False)
        details.append((truth, best if ok else None, checks))

    return {
        "chars": sum(len(t) for t in truth_texts), "edits": edits, "missed": missed, "texts": len(truth_texts),
        "junk": junk, "optional": len(optional), "optional_found": optional_found,
        "blocks": len(screen["blocks"]), "found": found, "grouped": grouped, "speaker": speaker_ok, "kind": kind_ok,
        "seconds": seconds, "details": details,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    default_captures = ROOT / "groundTruths" / "captures"
    if not default_captures.exists():
        default_captures = ROOT.parent / "groundTruths" / "captures"
    parser.add_argument("--captures", type=Path, default=default_captures)
    parser.add_argument("--truth", type=Path, default=ROOT / "evals" / "ground_truth.json")
    parser.add_argument("--show", action="store_true", help="print each block next to the truth")
    parser.add_argument("--upscale", action="store_true", help="enlarge small text before OCR (experimental)")
    parser.add_argument("--no-vote", action="store_true", help="don't correct uncertain lines by voting")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    screens = json.loads(args.truth.read_text(encoding="utf-8"))["screens"]
    source = RapidOcrSource(upscale=args.upscale, vote=not args.no_vote)
    print(f"{'screen':34} {'CER':>6} {'missed':>7} {'junk':>5} {'found':>6} {'grouped':>8} {'speaker':>8} {'kind':>6} {'optional':>9} {'time':>6}")
    totals: dict = {}
    for screen in screens:
        path = args.captures / screen["file"]
        if not path.exists():
            print(f"{screen['name']:34} skipped: {path} not found")
            continue
        r = score_screen(screen, Image.open(path).convert("RGB"), source)
        for key, value in r.items():
            if isinstance(value, (int, float)):
                totals[key] = totals.get(key, 0) + value
        totals["junk_count"] = totals.get("junk_count", 0) + len(r["junk"])
        n = r["blocks"]
        mark = "" if screen.get("verified") else "  (unverified)"
        print(
            f"{screen['name']:34} {r['edits'] / max(r['chars'], 1):6.1%} {r['missed']:>3}/{r['texts']:<3} {len(r['junk']):>5}"
            f" {r['found']:>3}/{n:<2} {r['grouped']:>4}/{n:<3} {r['speaker']:>4}/{n:<3} {r['kind']:>3}/{n:<2}"
            f" {r['optional_found']:>4}/{r['optional']:<4} {r['seconds']:5.2f}s{mark}"
        )
        if args.show:
            for truth, block, checks in r["details"]:
                problems = [k for k, v in checks.items() if not v]
                got = "(not found)" if block is None else f"[{block.kind}] {block.speaker + ': ' if block.speaker else ''}{' / '.join(l.text for l in block.lines)}"
                want = f"[{truth['kind']}] {truth['speaker'] + ': ' if truth.get('speaker') else ''}{' / '.join(truth['lines'])}"
                print(f"     {'ok' if not problems else '!! ' + ', '.join(problems)}\n        want {want}\n        got  {got}")
            for j in r["junk"]:
                print(f"     junk: {j}")

    if not totals:
        sys.exit(f"No screenshots found in {args.captures}")
    n = totals["blocks"]
    print(
        f"\n{'OVERALL':34} {totals['edits'] / max(totals['chars'], 1):6.1%} {totals['missed']:>3}/{totals['texts']:<3}"
        f" {totals['junk_count']:>5} {totals['found']:>3}/{n:<2} {totals['grouped']:>4}/{n:<3} {totals['speaker']:>4}/{n:<3}"
        f" {totals['kind']:>3}/{n:<2} {totals['optional_found']:>4}/{totals['optional']:<4} {totals['seconds']:5.2f}s"
    )


if __name__ == "__main__":
    main()
