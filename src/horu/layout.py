"""Turning detected lines into blocks: who is speaking, and what is dialogue.

Each block is either "dialogue" (has a speaker, or reads like a sentence) or "other" (button
legends, menu labels, HUD). Speakers are found by combining several signals rather than by
any single rule, because games style name tags very differently:

- the text itself: person names, 〜の声 ("voice of ..."), ？？？
- a different text style from the line below (colour, or light-on-dark vs dark-on-light)
- a different size from the line below (name tags are often bigger or slanted)

A speaker must sit directly above (or overlap) a body line that reads like dialogue, and
every speaker starts a new block. That keeps log/backlog screens, which stack many speaker +
line entries, apart, while menus (a highlighted item above other items) and big headers
above help text don't produce speakers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
from PIL import Image

from horu.corrections import Hints, normalize
from horu.text_sources.base import TextLine

# Hiragana, katakana, CJK ideographs (kanji), and the halfwidth katakana range.
_JAPANESE = re.compile(r"[぀-ヿ㐀-䶿一-鿿ｦ-ﾟ]")
_SENTENCE_PUNCTUATION = re.compile(r"[。、！？!?…‥「」『』]")
_NAME_LIKE = re.compile(r"声$|^[?？]{2,}$")
_MAX_SPEAKER_CHARS = 10
# A line wraps onto the next only if it's long (it filled the text box) or ends mid-sentence
# with punctuation; short labels stacked in a menu are separate items.
_MIN_WRAPPED_CHARS = 6
# Wrapped lines are set tightly (in every game seen so far the gap is at most ~10% of the
# line height); list items are spaced out (~70%).
_MAX_WRAP_GAP = 0.35
# Name tags are moderately bigger than dialogue (about 2-3x); titles and headers are bigger
# still, so size only counts within this range.
_NAME_TAG_SIZE = (1.6, 3.2)
_WRAP_END = re.compile(r"[、。！？!?…‥」』]$")
# A sentence broken mid-way ends on punctuation or hiragana (a particle or verb ending: に, が,
# て); a line ending on a katakana or kanji noun is usually a complete label.
_MID_SENTENCE_END = re.compile(r"[、。！？!?…‥」』\u3041-\u309f]$")
# Furigana printed by the game itself: a short line of kana, much smaller than the line it
# sits on top of. Horu adds its own readings, so these are dropped.
_KANA_ONLY = re.compile(r"[\u3041-\u3096\u30a1-\u30faー・]+")
_FURIGANA_MAX_SIZE = 0.75  # of the annotated line's height
_MIN_BLOCK_CHARS = 2
_LONG_TEXT_CHARS = 12  # longer than any button label or menu item
_SENTENCE_FINAL_PARTICLES = {"か", "よ", "ね", "な", "ぞ", "ぜ", "わ", "さ", "の", "かな", "よね"}

# Button legends: a key name or key icon followed by a UI command ("F 早送り", "区オート").
# OCR often reads a boxed key icon as a kanji-like box: 区, 口, ロ, 囗. The label has to be a
# known command, since a letter or box-like kana also starts ordinary words (Tシャツ, ロボット).
_KEY = r"(?:(?:Tab|Shift|Space|Esc|Enter|Ctrl|Alt|Back|Start|Select|Home)(?![a-z])|[LR][123]|[A-Z](?![a-z])|[区口ロ囗□■])"
_KEY_THEN_LABEL = re.compile(rf"^{_KEY}\s*(.+)$")
# Dialogue never starts with a multi-letter key name, so these are legends however the rest
# was read ("Tab メニュー Shift ダッシュ" often comes out garbled).
_KEY_NAME_START = re.compile(r"^(?:Tab|Shift|Space|Esc|Enter|Ctrl|Alt|Back|Start|Select|Home)\b")
# Common game UI commands, recognised on their own when the key icon wasn't read.
_UI_WORDS = {
    "決定", "戻る", "キャンセル", "スキップ", "オート", "早送り", "ログ", "メニュー", "ダッシュ",
    "音声再生", "閉じる", "選択", "確認", "次へ", "前へ", "切替", "切り替え", "詳細", "ヘルプ",
    "セーブ", "ロード", "調べる", "話す", "マップ", "カメラ", "移動", "拡大", "縮小", "並び替え",
    "表示", "非表示", "削除", "変更", "購入", "売却", "装備", "ガード", "ジャンプ", "ページ送り",
}


@dataclass(frozen=True)
class TextBlock:
    lines: tuple[TextLine, ...]
    speaker: str | None = None
    kind: str = "dialogue"  # "dialogue" or "other"
    speaker_score: float = 1.0  # OCR confidence for the speaker line

    @property
    def text(self) -> str:
        # Japanese has no spaces between words, so wrapped lines join directly.
        return "".join(line.text for line in self.lines)

    @property
    def box(self) -> tuple[int, int, int, int]:
        return (
            min(l.box[0] for l in self.lines),
            min(l.box[1] for l in self.lines),
            max(l.box[2] for l in self.lines),
            max(l.box[3] for l in self.lines),
        )


@dataclass(frozen=True)
class Style:
    """How a line's text is drawn: its colour, and whether it's lighter than its background."""

    ink: tuple[float, float, float]
    light_on_dark: bool

    def differs_from(self, other: Style) -> bool:
        if self.light_on_dark != other.light_on_dark:
            return True
        return float(np.linalg.norm(np.subtract(self.ink, other.ink))) > 70


def is_japanese(text: str) -> bool:
    return bool(_JAPANESE.search(text))


def is_legend(text: str) -> bool:
    compact = text.replace(" ", "").replace("　", "")
    if compact in _UI_WORDS or _KEY_NAME_START.match(text.strip()):
        return True
    match = _KEY_THEN_LABEL.match(text.strip()) or _KEY_THEN_LABEL.match(compact)
    return bool(match) and match.group(1).replace(" ", "") in _UI_WORDS


def line_style(image: Image.Image, box: tuple[int, int, int, int]) -> Style | None:
    """Measure a line's text colour: split the box into ink and background by brightness,
    taking the smaller group as ink."""
    px = np.asarray(image.crop(box).convert("RGB"), dtype=np.float32).reshape(-1, 3)
    if len(px) < 16:
        return None
    lum = px @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    light = lum > _otsu(lum)
    ink_is_light = light.sum() < (~light).sum()
    ink = px[light if ink_is_light else ~light]
    if len(ink) == 0:
        return None
    return Style(tuple(float(c) for c in ink.mean(0)), bool(ink_is_light))


def group_blocks(
    lines: list[TextLine],
    image: Image.Image | None = None,
    styles: list[Style | None] | None = None,
    hints: Hints | None = None,
) -> list[TextBlock]:
    """Group lines into blocks in reading order.

    Text styles are measured from `image`, or given directly as `styles` (one per line, for
    tests). Without either, speakers are found from text and size alone. `hints` are the
    user's corrections for this game, and win over the automatic rules.
    """
    hints = hints or Hints()
    if styles is None:
        styles = [line_style(image, l.box) if image is not None else None for l in lines]
    kept = [(l, st) for l, st in zip(lines, styles) if is_japanese(l.text)]
    # Game-drawn furigana would otherwise be read as speakers or lines of their own.
    all_lines = [o for o, _ in kept]
    furigana = [l for l in all_lines if _is_furigana(l, all_lines)]
    kept = [(l, st) for l, st in kept if l not in furigana]
    lines = [l for l, _ in kept]
    # Furigana printed above a line widens the gap above it: the rows are set apart to fit it.
    ruby = {i: _ruby_height(l, furigana) for i, l in enumerate(lines)}
    styles = {i: st for i, (_, st) in enumerate(kept) if st is not None}
    order = sorted(range(len(lines)), key=lambda i: (lines[i].box[1], lines[i].box[0]))
    legends = {i for i in order if is_legend(lines[i].text) and normalize(lines[i].text) not in hints.dialogue}
    speakers = {i for i in order if i not in legends and _is_speaker(i, lines, styles, legends, hints)}

    groups: list[dict] = []  # {"speaker": index or None, "lines": [indices]}
    for i in order:
        line = lines[i]
        if i in legends:
            groups.append({"speaker": None, "lines": [i], "legend": True})
            continue
        if i in speakers:
            groups.append({"speaker": i, "lines": []})
            continue
        for group in groups:
            if group.get("legend"):
                continue
            if group["lines"]:
                attaches = _continues(lines[group["lines"][-1]], line, ruby[i])
            else:  # a speaker still waiting for its first line
                attaches = _under_speaker(lines[group["speaker"]], line)
            if attaches:
                group["lines"].append(i)
                break
        else:
            groups.append({"speaker": None, "lines": [i]})
    _join_centred(groups, lines, ruby)

    blocks = []
    for group in groups:
        if not group["lines"]:  # a speaker whose line wasn't found: show it as text instead
            group = {"speaker": None, "lines": [group["speaker"]]}
        block_lines = tuple(lines[i] for i in group["lines"])
        speaker = lines[group["speaker"]].text if group["speaker"] is not None else None
        speaker_score = lines[group["speaker"]].score if group["speaker"] is not None else 1.0
        text = "".join(l.text for l in block_lines)
        # Lone characters are almost always icons or decorations misread as kanji.
        if len(text) < _MIN_BLOCK_CHARS and speaker is None:
            continue
        kind = "other" if group.get("legend") else ("dialogue" if speaker or _reads_like_dialogue(text) else "other")
        if normalize(text) in hints.dialogue:
            kind = "dialogue"
        elif normalize(text) in hints.other:
            kind = "other"
        blocks.append(TextBlock(block_lines, speaker, kind, speaker_score))
    return blocks


# --- Speakers --------------------------------------------------------------------------


def _is_speaker(i: int, lines: list[TextLine], styles: dict[int, Style], legends: set[int], hints: Hints) -> bool:
    candidate = lines[i]
    text = candidate.text.strip()
    marked = normalize(text) in hints.speaker
    if normalize(text) in hints.not_speaker:
        return False
    if not marked and (
        len(_JAPANESE.findall(text)) < 2 and not _NAME_LIKE.search(text)
        or len(text) > _MAX_SPEAKER_CHARS
        or (_SENTENCE_PUNCTUATION.search(text) and not _NAME_LIKE.search(text))
    ):
        return False
    below = [j for j in range(len(lines)) if j != i and j not in legends and _under_speaker(candidate, lines[j])]
    if not below:
        return False
    if marked:
        return True
    body = min(below, key=lambda j: lines[j].box[1])
    # Name tags sit at the text's left edge (or left of it). A heading centred over the text,
    # well in from its edge, is a title: a sign's place name (ゾーラ川 over 急流に注意！).
    c_left, _, c_right, _ = candidate.box
    b_left, b_top, b_right, b_bottom = lines[body].box
    b_height = b_bottom - b_top
    centred = abs((c_left + c_right) - (b_left + b_right)) / 2 <= 0.5 * b_height
    if not marked and centred and c_left - b_left > 2 * b_height:
        return False
    # A speaker says something: the line below must read like dialogue, not be another menu
    # item or label.
    if not _reads_like_dialogue(lines[body].text):
        return False

    score = 0
    if _NAME_LIKE.search(text) or normalize(text) in hints.known_speakers or _has_person_name(text):
        score += 2
    if i in styles and body in styles and styles[i].differs_from(styles[body]):
        score += 2
    ratio = _height(candidate) / max(_height(lines[body]), 1)
    low, high = _NAME_TAG_SIZE
    if low <= ratio <= high:
        score += 2
    elif 1.25 <= ratio < low or ratio <= 0.8:
        score += 1
    return score >= 2


def _under_speaker(speaker: TextLine, line: TextLine) -> bool:
    """`line` is the first body line of `speaker`: directly below it (or overlapping its
    lower part, as highlighted log entries do) and starting near it horizontally."""
    s_left, s_top, s_right, s_bottom = speaker.box
    left, top, right, bottom = line.box
    height = bottom - top
    if top <= s_top:
        return False
    gap = top - s_bottom
    close_below = -0.6 * (s_bottom - s_top) <= gap <= 1.5 * height
    near = min(s_right, right) > max(s_left, left) or abs(left - s_left) <= 3 * height
    return close_below and near


def _has_person_name(text: str) -> bool:
    from horu.nlp.words import tokenize

    return any(t.pos2 == "固有名詞" and t.pos3 == "人名" for t in tokenize(text))


# --- Dialogue vs other text ------------------------------------------------------------


def _reads_like_dialogue(text: str) -> bool:
    # Count only Japanese characters: digits and stray Latin (HP/SP values, misreads) don't
    # make a label a sentence.
    if len(_JAPANESE.findall(text)) >= _LONG_TEXT_CHARS or _SENTENCE_PUNCTUATION.search(text):
        return True
    from horu.nlp.words import tokenize

    content = [t for t in tokenize(text) if t.pos1 not in ("補助記号", "記号", "空白")]
    if not content:
        return False
    last = content[-1]
    # Ends like a sentence: a copula or auxiliary (あそこだ, 行きます) or a sentence-final
    # particle (そうか, いいよ).
    # UniDic doesn't always tag these as sentence-final (そうか's か is "adverbial"), so the
    # common ones count by spelling too.
    return last.pos1 == "助動詞" or (
        last.pos1 == "助詞" and (last.pos2 == "終助詞" or last.surface in _SENTENCE_FINAL_PARTICLES)
    )


def _continues(prev: TextLine, line: TextLine, ruby: int = 0) -> bool:
    p_left, p_top, p_right, p_bottom = prev.box
    left, top, right, bottom = line.box
    p_height, height = p_bottom - p_top, bottom - top
    similar_size = max(p_height, height) <= 1.5 * min(p_height, height)
    # Tightly set lines' boxes can overlap by up to about half a line.
    # Measured against the smaller line, since an icon beside a menu item can inflate its box.
    close_below = top > p_top and -0.5 * p_height <= top - p_bottom <= _MAX_WRAP_GAP * min(p_height, height) + ruby
    overlaps = min(p_right, right) > max(p_left, left)
    wraps = len(_JAPANESE.findall(prev.text)) >= _MIN_WRAPPED_CHARS or bool(_WRAP_END.search(prev.text.strip()))
    # Right-aligned stacks with ragged left edges are menus; wrapped text starts each line at
    # the same left edge (or is centred).
    right_aligned_list = abs(right - p_right) <= 0.5 * p_height and abs(left - p_left) > p_height
    # The same goes for indented lines: a wrapped line starts where the one above did, or both
    # are centred. Choices indented under a question are separate items.
    aligned = abs(left - p_left) <= p_height or abs((left + right) - (p_left + p_right)) / 2 <= p_height
    # Text wraps when a line is full, so the next line doesn't run far past it, unless the
    # first line broke mid-sentence. A label followed by a longer line is two list items
    # (e.g. skill names in a battle menu).
    runs_past = right - p_right > p_height
    ends_like_label = not _MID_SENTENCE_END.search(prev.text.strip())
    two_items = runs_past and ends_like_label
    return (
        similar_size
        and close_below
        and overlaps
        and wraps
        and aligned
        and not right_aligned_list
        and not two_items
        and not _ends_complete_without_punctuation(prev.text)
    )


def _join_centred(groups: list[dict], lines: list[TextLine], ruby: dict[int, int] | None = None) -> None:
    """Join centred text that wraps unevenly. In a centred dialogue box lines can be any
    length (よく　きたな　Luke。／ここが　聖なる山／デスマウンテンの山頂じゃ。), which the
    left-aligned wrap rules read as separate labels. A tight centred stack is one block when it
    ends in sentence punctuation; centred menus (はじめから／つづきから) don't."""
    for k in range(len(groups) - 1, 0, -1):
        later = groups[k]
        if later.get("legend") or later["speaker"] is not None or not later["lines"]:
            continue
        if not _WRAP_END.search(lines[later["lines"][-1]].text.strip()):
            continue
        first = lines[later["lines"][0]]
        for earlier in groups[:k]:
            if earlier.get("legend") or not earlier["lines"]:
                continue
            if _centred_below(lines[earlier["lines"][-1]], first, (ruby or {}).get(later["lines"][0], 0)):
                earlier["lines"].extend(later["lines"])
                del groups[k]
                break


def _centred_below(prev: TextLine, line: TextLine, ruby: int = 0) -> bool:
    p_left, p_top, p_right, p_bottom = prev.box
    left, top, right, bottom = line.box
    p_height, height = p_bottom - p_top, bottom - top
    similar_size = max(p_height, height) <= 1.5 * min(p_height, height)
    close_below = top > p_top and -0.5 * p_height <= top - p_bottom <= _MAX_WRAP_GAP * min(p_height, height) + ruby
    centred = abs((left + right) - (p_left + p_right)) / 2 <= 0.5 * p_height
    return similar_size and close_below and centred


def _ends_complete_without_punctuation(text: str) -> bool:
    """A line ending in a plain (dictionary-form) verb or adjective with no punctuation is a
    finished phrase, typically a menu choice (ヒント映像を見る), not a sentence that wraps."""
    if _WRAP_END.search(text.strip()):
        return False
    from horu.nlp.words import tokenize

    content = [t for t in tokenize(text) if t.pos1 not in ("補助記号", "記号", "空白")]
    if not content:
        return False
    last = content[-1]
    return last.pos1 in ("動詞", "形容詞") and last.c_form.startswith("終止形")


def _is_furigana(line: TextLine, lines: list[TextLine]) -> bool:
    text = line.text.replace(" ", "").replace("\u3000", "")
    # Furigana is kana, but small kana gets misread: \u307e\u307b\u3046 as \u624b\u307b\u3046. One other character is
    # allowed in a reading of three or more.
    others = len(text) - sum(len(run) for run in _KANA_ONLY.findall(text))
    if not text or others > (1 if len(text) >= 3 else 0):
        return False
    left, top, right, bottom = line.box
    height = bottom - top
    center = (left + right) / 2
    for other in lines:
        if other is line:
            continue
        o_left, o_top, o_right, o_bottom = other.box
        smaller = height <= _FURIGANA_MAX_SIZE * (o_bottom - o_top)
        just_above = -0.6 * height <= o_top - bottom <= 0.6 * height
        if smaller and just_above and o_left <= center <= o_right:
            return True
    return False


def _ruby_height(line: TextLine, furigana: list[TextLine]) -> int:
    """The height of the furigana printed just above `line`, or 0."""
    left, top, right, _ = line.box
    heights = []
    for f in furigana:
        f_left, f_top, f_right, f_bottom = f.box
        height = f_bottom - f_top
        if left <= (f_left + f_right) / 2 <= right and -0.6 * height <= top - f_bottom <= 0.6 * height:
            heights.append(height)
    return max(heights, default=0)


def _height(line: TextLine) -> int:
    return line.box[3] - line.box[1]


def _otsu(values: np.ndarray) -> float:
    """The brightness threshold that best separates two groups (Otsu's method)."""
    hist, _ = np.histogram(values, bins=256, range=(0, 256))
    levels = np.arange(256)
    weight0 = np.cumsum(hist)
    weight1 = values.size - weight0
    sum0 = np.cumsum(hist * levels)
    with np.errstate(divide="ignore", invalid="ignore"):
        mean0 = sum0 / weight0
        mean1 = (sum0[-1] - sum0) / weight1
        between = weight0 * weight1 * (mean0 - mean1) ** 2
    return float(np.nanargmax(between))
