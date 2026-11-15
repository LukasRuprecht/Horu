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
from dataclasses import dataclass, replace

import numpy as np
from PIL import Image

from horu.corrections import Hints, normalize
from horu.text_sources.base import TextLine

# Hiragana, katakana, CJK ideographs (kanji), and the halfwidth katakana range.
_JAPANESE = re.compile(r"[぀-ヿ㐀-䶿一-鿿ｦ-ﾟ]")
_SENTENCE_PUNCTUATION = re.compile(r"[。、！？!?…‥]")
# Quotes mark speech only in pairs: a lone 『 or 」 at a line's edge is usually an icon misread.
_QUOTED = re.compile(r"[「『].*[」』]")
# Below this OCR confidence, a short line without a speaker is a garbled read of scenery (a
# sign in perspective), not dialogue.
_GARBLED = 0.65
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
# A line opening with an ellipsis (as OCR reads what's left of one: …, ‥, ・, or a full stop),
# and how far in such a line may start, in line heights: …… is two characters wide.
_LEADING_DOTS = re.compile(r"[…‥・.．。]")
_DOTS_WIDTH = 2.5
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
    titled: bool = False  # the first line is a heading over the rest (a sign's place name)
    speaker_line: TextLine | None = None  # the name tag as read: its box and other readings

    @property
    def text(self) -> str:
        # Japanese has no spaces between words, so wrapped lines join directly. A title is
        # set apart, so it isn't read as part of the first word.
        if self.titled and len(self.lines) > 1:
            return self.lines[0].text + "　" + "".join(line.text for line in self.lines[1:])
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
    lines = _unclip_leading_dots([l for l, _ in kept])
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
                # Under a name tag even a short first line wraps (Yakuza's あ、桐生さん over
                # ちょっと職務質問お願いします！): menus' short stacked labels have no tag.
                attaches = _continues(lines[group["lines"][-1]], line, ruby[i], short_wraps=group["speaker"] is not None)
            else:  # a speaker still waiting for its first line
                attaches = _under_speaker(lines[group["speaker"]], line)
            if attaches:
                group["lines"].append(i)
                break
        else:
            groups.append({"speaker": None, "lines": [i]})
    _join_centred(groups, lines, ruby)
    _join_titles(groups, lines, styles, ruby)

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
        garbled = speaker is None and max(l.score for l in block_lines) < _GARBLED
        kind = "other" if group.get("legend") or garbled else ("dialogue" if speaker or _reads_like_dialogue(text) else "other")
        speaker_line = lines[group["speaker"]] if group["speaker"] is not None else None
        blocks.append(TextBlock(block_lines, speaker, kind, speaker_score, bool(group.get("titled")), speaker_line))
    blocks = _choices_follow(blocks)
    for i, block in enumerate(blocks):
        if normalize(block.text) in hints.dialogue:
            blocks[i] = replace(block, kind="dialogue")
        elif normalize(block.text) in hints.other:
            blocks[i] = replace(block, kind="other")
    return blocks


def _choices_follow(blocks: list[TextBlock]) -> list[TextBlock]:
    """Choices are offered as a stack of short lines; when one reads like something said
    (道を訊きたい), or they follow a question, the others that are phrases are dialogue too
    (用は無い ends like a menu label would). So are stacked phrases offered while someone
    is speaking (気をつける／適当にやる under 佐倉惣治郎's line): menus don't come with a name
    tag. A tilted line in a stack of choices is one too, phrase or not (ごちそうさま over
    旨かった／目が覚めた): menus aren't drawn at an angle among them."""
    blocks = list(blocks)

    def could_be_choice(block: TextBlock, tilted_ok: bool = False) -> bool:
        return (
            block.kind == "other"
            and block.speaker is None
            and len(block.lines) == 1
            and not is_legend(block.text)
            and (_is_phrase(block.text) or (tilted_ok and bool(block.lines[0].slant_height)))
        )

    if any(b.kind == "dialogue" and b.speaker for b in blocks):
        offered = [i for i, b in enumerate(blocks) if could_be_choice(b)]
        stack = [i for i in offered if any(j != i and _stacked(blocks[i].lines[0], blocks[j].lines[0]) for j in offered)]
        for i in stack:
            blocks[i] = replace(blocks[i], kind="dialogue")
    changed = True
    while changed:
        changed = False
        said = [b for b in blocks if b.kind == "dialogue" and b.speaker is None]
        tilted = any(l.slant_height for b in said for l in b.lines)
        for i, block in enumerate(blocks):
            if could_be_choice(block, tilted) and any(
                _stacked(block.lines[0], other.lines[-1]) or _stacked(block.lines[0], other.lines[0]) for other in said
            ):
                blocks[i] = replace(block, kind="dialogue")
                changed = True
    return blocks


def _stacked(a: TextLine, b: TextLine) -> bool:
    """Two lines of about the same size, one just above the other and overlapping it: items
    of one list."""
    height = min(_height(a), _height(b))
    upper, lower = sorted((a, b), key=lambda l: l.box[1])
    gap = lower.box[1] - upper.box[3]
    overlaps = min(a.box[2], b.box[2]) > max(a.box[0], b.box[0])
    return max(_height(a), _height(b)) <= 1.5 * height and -0.5 * height <= gap <= 2 * height and overlaps


def _is_phrase(text: str) -> bool:
    """Has a verb, adjective, or auxiliary: says something, unlike a noun label."""
    from horu.nlp.words import tokenize

    return any(t.pos1 in ("動詞", "形容詞", "助動詞") for t in tokenize(text))


# --- Speakers --------------------------------------------------------------------------


def _is_speaker(i: int, lines: list[TextLine], styles: dict[int, Style], legends: set[int], hints: Hints) -> bool:
    candidate = lines[i]
    text = candidate.text.strip()
    marked = normalize(text) in hints.speaker
    if normalize(text) in hints.not_speaker:
        return False
    if not marked and (
        _reads_like_dialogue(text) and not _NAME_LIKE.search(text)
        or len(_JAPANESE.findall(text)) < 2 and not _NAME_LIKE.search(text)
        or len(text) > _MAX_SPEAKER_CHARS
        or (_has_sentence_punctuation(text) and not _NAME_LIKE.search(text))
    ):
        return False
    below = [j for j in range(len(lines)) if j != i and j not in legends and _under_speaker(candidate, lines[j])]
    if not below:
        return False
    if marked:
        return True
    body = min(below, key=lambda j: lines[j].box[1])
    # Name tags sit at the text's left edge (or left of it). A line starting well in from it is
    # a heading: a sign's place name (ゾーラ川 over 急流に注意！), a tutorial's title.
    c_left = candidate.box[0]
    b_left, b_top, _, b_bottom = lines[body].box
    if not marked and c_left - b_left > 2 * (b_bottom - b_top):
        return False
    # A speaker says something: the line below must read like dialogue, not be another menu
    # item or label.
    if not _reads_like_dialogue(lines[body].text):
        return False
    # Tilted choices (P5R's ごちそうさま／旨かった／目が覚めた): the line below is the next
    # choice, not what a tilted name tag's speaker says, when it's tilted too (シュージンのはず
    # over そっちもシュージンか？), or a line stacked under it is. Text under a tilted name tag
    # is level. (OCR finds some choices level.)
    if candidate.slant_height and lines[body].slant_height:
        return False
    if candidate.slant_height and any(
        lines[j].slant_height and lines[j].box[1] > lines[body].box[1] and _stacked(lines[body], lines[j])
        for j in range(len(lines))
        if j not in (i, body) and j not in legends
    ):
        return False

    score = 0
    if _NAME_LIKE.search(text) or normalize(text) in hints.known_speakers or _has_person_name(text):
        score += 2
    if i in styles and body in styles and styles[i].differs_from(styles[body]):
        score += 2
    elif bool(candidate.slant_height) != bool(lines[body].slant_height):
        # Set at an angle to the text below: a tag made to stand out (P5R's tilted names).
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


def _has_sentence_punctuation(text: str) -> bool:
    return bool(_SENTENCE_PUNCTUATION.search(text) or _QUOTED.search(text))


def _reads_like_dialogue(text: str) -> bool:
    if _has_sentence_punctuation(text):
        return True
    from horu.nlp.words import tokenize

    content = [t for t in tokenize(text) if t.pos1 not in ("補助記号", "記号", "空白")]
    if not content:
        return False
    # Long text is a sentence even without punctuation, if it has the particles or endings of
    # one: a sign's run of nouns (武見内科医院　休診日　日曜・祝日) is not. Only Japanese
    # characters count: digits and stray Latin (HP/SP values, misreads) don't make a label a
    # sentence.
    if len(_JAPANESE.findall(text)) >= _LONG_TEXT_CHARS:
        return any(t.pos1 in ("助詞", "助動詞") for t in content)
    last = content[-1]
    # Ends like a sentence: a copula or auxiliary (あそこだ, 行きます) or a sentence-final
    # particle (そうか, いいよ).
    # The tokenizer doesn't always tag these as sentence-final (そうか's か is "adverbial"), so the
    # common ones count by spelling too.
    return last.pos1 == "助動詞" or (
        last.pos1 == "助詞" and (last.pos2 == "終助詞" or last.surface in _SENTENCE_FINAL_PARTICLES)
    )


def _continues(prev: TextLine, line: TextLine, ruby: int = 0, short_wraps: bool = False) -> bool:
    p_left, p_top, p_right, p_bottom = prev.box
    left, top, right, bottom = line.box
    p_height, height = p_bottom - p_top, bottom - top
    similar_size = max(p_height, height) <= 1.5 * min(p_height, height)
    # Tightly set lines' boxes can overlap by up to about half a line.
    # Measured against the smaller line, since an icon beside a menu item can inflate its box.
    close_below = top > p_top and -0.5 * p_height <= top - p_bottom <= _MAX_WRAP_GAP * min(p_height, height) + ruby
    overlaps = min(p_right, right) > max(p_left, left)
    wraps = short_wraps or len(_JAPANESE.findall(prev.text)) >= _MIN_WRAPPED_CHARS or bool(_WRAP_END.search(prev.text.strip()))
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


def _unclip_leading_dots(lines: list[TextLine]) -> list[TextLine]:
    """A line that opens with an ellipsis is often boxed from its first real character: the
    dots sit low and faint (Yakuza's ……立華不動産の人間が over って聞いてきたんだが), so it
    looks indented, and neither wraps onto the line under it nor sits under its name tag.
    Such a line starts where the line set tightly above or below it starts."""
    out = list(lines)
    for i, line in enumerate(lines):
        if not _LEADING_DOTS.match(line.text.strip()):
            continue
        left, top, right, bottom = line.box
        height = bottom - top
        for other in lines:
            o_left, o_top, o_right, o_bottom = other.box
            o_height = o_bottom - o_top
            if other is line or max(height, o_height) > 1.5 * min(height, o_height):
                continue
            gap = o_top - bottom if o_top > top else top - o_bottom
            tight = -0.5 * height <= gap <= _MAX_WRAP_GAP * min(height, o_height)
            indented = 0 < left - o_left <= _DOTS_WIDTH * height and o_right > left
            if tight and indented:
                out[i] = replace(line, box=(o_left, top, right, bottom))
                break
    return out


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


def _join_titles(groups: list[dict], lines: list[TextLine], styles: dict[int, Style], ruby: dict[int, int]) -> None:
    """Join a title to the text under it: a short line in its own colour just above a block,
    within its width (ゾーラ川 over a sign's 急流に注意！トコトン流されます; a P5R tutorial's
    title bar). The colour keeps centred menus (はじめから／つづきから) apart."""
    for k in range(len(groups) - 2, -1, -1):
        title = groups[k]
        if title.get("legend") or title["speaker"] is not None or len(title["lines"]) != 1:
            continue
        t = title["lines"][0]
        text = lines[t].text.strip()
        if len(text) > _MAX_SPEAKER_CHARS or _WRAP_END.search(text):
            continue
        for body in groups[k + 1:]:
            if body.get("legend") or body["speaker"] is not None or not body["lines"]:
                continue
            first = body["lines"][0]
            own_colour = t in styles and first in styles and styles[t].differs_from(styles[first])
            body_box = _union_box([lines[i] for i in body["lines"]])
            # Centred over the first line (a sign's place name), or anywhere above a paragraph
            # (a tutorial's title bar); a highlighted menu item over the next is neither.
            above = _centred_below(lines[t], lines[first], ruby.get(first, 0)) or (
                len(body["lines"]) > 1 and _heading_above(lines[t], lines[first], body_box, ruby.get(first, 0))
            )
            if own_colour and above:
                body["lines"].insert(0, t)
                body["titled"] = True
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


def _heading_above(title: TextLine, first: TextLine, body: tuple[int, int, int, int], ruby: int = 0) -> bool:
    """`title` sits just above the block whose first line is `first`, within its width (a
    title bar's padding sets it a little further off than wrapped lines)."""
    t_left, t_top, t_right, t_bottom = title.box
    t_height, height = _height(title), _height(first)
    similar_size = max(t_height, height) <= 1.6 * min(t_height, height)
    gap = first.box[1] - t_bottom
    close_above = first.box[1] > t_top and -0.5 * t_height <= gap <= 0.8 * min(t_height, height) + ruby
    within = t_left >= body[0] - height and t_right <= body[2] + height
    return similar_size and close_above and within


def _union_box(lines: list[TextLine]) -> tuple[int, int, int, int]:
    return (
        min(l.box[0] for l in lines),
        min(l.box[1] for l in lines),
        max(l.box[2] for l in lines),
        max(l.box[3] for l in lines),
    )


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


def _height(line: TextLine) -> float:
    """The text's height: for a slanted line, across its slant, not its box's."""
    return line.slant_height or line.box[3] - line.box[1]


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
    if np.isnan(between).all():  # one flat colour: nothing to separate
        return float(values.max())
    return float(np.nanargmax(between))
