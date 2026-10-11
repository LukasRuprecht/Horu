"""Score Horu's word splits against evals/word_splits.json.

    uv run python evals/eval_words.py            # summary and every sentence that's off
    uv run python evals/eval_words.py --quiet    # summary only

A sentence is right when every required boundary (a space in the truth) is there and no
boundary appears where the truth has none ('|' boundaries may go either way). "Split" counts
boundaries inside a true word (日本 + 語); "joined" counts true boundaries that are missing
(とか where と + か is right).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from horu.dictionary import Dictionary  # noqa: E402
from horu.nlp import analyze  # noqa: E402


def boundaries(words: str) -> tuple[set[int], set[int]]:
    """(required, optional) character offsets where a word ends, from "a b|c" notation."""
    required, optional, position = set(), set(), 0
    for char in words:
        if char == " ":
            required.add(position)
        elif char == "|":
            optional.add(position)
        else:
            position += 1
    return required, optional


def predicted(words: list[str]) -> set[int]:
    cuts, position = set(), 0
    for word in words[:-1]:
        position += len(word)
        cuts.add(position)
    return cuts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--truth", type=Path, default=ROOT / "evals" / "word_splits.json")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    dictionary = Dictionary()
    sentences = json.loads(args.truth.read_text(encoding="utf-8"))["sentences"]
    right = split = joined = 0
    for sentence in sentences:
        words = [w.surface for w in analyze(sentence["text"], dictionary.has_form, dictionary.kinds)]
        required, optional = boundaries(sentence["words"])
        cuts = predicted(words)
        extra = cuts - required - optional
        missing = required - cuts
        split += len(extra)
        joined += len(missing)
        if not extra and not missing:
            right += 1
        elif not args.quiet:
            print(f"  truth: {sentence['words']}\n  horu:  {' '.join(words)}\n")
    total = len(sentences)
    print(f"{right}/{total} sentences right ({right / total:.0%}); {split} wrong splits, {joined} wrong joins")


if __name__ == "__main__":
    main()
