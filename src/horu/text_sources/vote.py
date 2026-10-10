"""Character-level voting between OCR readings of the same line.

Different recognition models make different mistakes. When the main reading is uncertain,
the line is read again by other models, each reading is aligned to the main one, and a
character is changed (or inserted) only when most readers agree. One reader's hallucination
can't outvote the rest, so voting fixes misreads without introducing new ones.
"""

from __future__ import annotations

from collections import Counter


def align(base: str, other: str) -> tuple[list[str], list[str]]:
    """Align `other` to `base` by edit distance.

    Returns, for each character of `base`, what `other` has in its place ("" if deleted),
    and for each gap between characters (len(base) + 1 of them), text `other` inserts there.
    """
    n, m = len(base), len(other)
    cost = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        cost[i][0] = i
    for j in range(m + 1):
        cost[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost[i][j] = min(cost[i - 1][j] + 1, cost[i][j - 1] + 1, cost[i - 1][j - 1] + (base[i - 1] != other[j - 1]))

    replaced = [""] * n
    inserted = [""] * (n + 1)
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and cost[i][j] == cost[i - 1][j - 1] + (base[i - 1] != other[j - 1]):
            replaced[i - 1] = other[j - 1]
            i, j = i - 1, j - 1
        elif i > 0 and cost[i][j] == cost[i - 1][j] + 1:
            replaced[i - 1] = ""
            i -= 1
        else:
            inserted[i] = other[j - 1] + inserted[i]
            j -= 1
    return replaced, inserted


def vote(base: str, others: list[str]) -> str:
    """`base`, with each character changed only where a strict majority of all readers
    (base included) agree on the same change."""
    voters = len(others) + 1
    alignments = [align(base, other) for other in others]
    result = []
    for gap in range(len(base) + 1):
        inserts = Counter(inserted[gap] for _, inserted in alignments if inserted[gap])
        if inserts:
            text, count = inserts.most_common(1)[0]
            if count > voters / 2:
                result.append(text)
        if gap < len(base):
            choices = Counter(replaced[gap] for replaced, _ in alignments)
            choices[base[gap]] += 1
            best, count = choices.most_common(1)[0]
            result.append(best if count > voters / 2 else base[gap])
    return "".join(result)


def edit_distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


# Full-size kana and their small forms. Whether a kana is small is only clear next to the
# rest of the line, so a short piece read on its own often gets it wrong.
_SIZES = dict(zip("あいうえおつやゆよわかけアイウエオツヤユヨワカケ", "ぁぃぅぇぉっゃゅょゎゕゖァィゥェォッャュョヮヵヶ"))
_SIZE_PAIRS = {*_SIZES.items(), *((small, big) for big, small in _SIZES.items())}


def take_kana_sizes(base: str, other: str) -> str:
    """`base` with each kana resized to match `other`, a reading of the same text with more
    context. Nothing else is taken from `other`."""
    replaced, _ = align(base, other)
    return "".join(r if (c, r) in _SIZE_PAIRS else c for c, r in zip(base, replaced))
