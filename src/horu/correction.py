"""Deciding what to do with a proposed fix for a line Horu may have misread.

A fix is applied automatically only when two independent sources agree on it: the AI's
reading matches what one of the other OCR models read. A single source (a vision model's
reading nobody else had, or a text-only model's guess while explaining) is only offered to
the learner as a suggestion, because a wrong "fix" would teach them Japanese that isn't in
the game.
"""

from __future__ import annotations

import re
import unicodedata

from horu.text_sources.vote import edit_distance

SAME = "same"
APPLY = "apply"
SUGGEST = "suggest"
IGNORE = "ignore"
# The largest share of a line's characters a fix may change.
_MAX_CHANGE = 0.3

# A model that suspects a misread starts its explanation with this line (see tutor.SYSTEM).
_OCR_LINE = re.compile(r"^\s*OCR:\s*(.+?)\s*$", re.M)
_READING = re.compile(r"[（(][ぁ-ゖァ-ヺー・]+[）)]")


def plain(text: str) -> str:
    """Text without spacing, width (！ vs !) or quote differences."""
    # NFKC spells … as "...", which would count as three characters.
    return re.sub(r"[\s「」『』]", "", unicodedata.normalize("NFKC", text)).replace("...", "…")


def decide(text: str, proposal: str, confirmations: list[str]) -> str:
    """SAME if `proposal` says what `text` already says; IGNORE if it rewrites the line;
    APPLY if another source in `confirmations` read the same thing; otherwise SUGGEST."""
    if not proposal.strip() or plain(proposal) == plain(text):
        return SAME
    # OCR misreads a few characters. A proposal far from what was read is a model
    # rewriting text it doesn't understand (dialect: てやんでぇべらぼうめい！ "fixed" to
    # ベルがいいね！), or a garbled reading, not a fix.
    if edit_distance(plain(proposal), plain(text)) > max(2, _MAX_CHANGE * len(plain(text))):
        return IGNORE
    if any(_agrees(proposal, other, text) for other in confirmations if other):
        return APPLY
    return SUGGEST


def _agrees(proposal: str, other: str, text: str) -> bool:
    """`other` read what `proposal` says, give or take a character, and is closer to it than
    the original reading is (太陽は月に…月は太陽に… vs 太陽は月に月は太陽に…, both restoring
    the 月 OCR lost)."""
    p, o, t = plain(proposal), plain(other), plain(text)
    return p == o or (edit_distance(p, o) <= 1 and edit_distance(p, o) < edit_distance(p, t))


def without_proposal(reply: str) -> str:
    """The reply with its leading "OCR: ..." line removed."""
    if proposed_in(reply) is None:
        return reply.strip()
    parts = reply.lstrip().split("\n", 1)
    return parts[1].strip() if len(parts) > 1 else ""


def proposed_in(reply: str) -> str | None:
    """The corrected line a model proposed at the start of its reply, if any."""
    head = reply.lstrip().split("\n", 1)[0]
    match = _OCR_LINE.match(head)
    if not match:
        return None
    # Models like to add readings: 太陽（たいよう）は月に…
    return _READING.sub("", match.group(1)).strip("「」『』")
