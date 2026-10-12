"""Text detection + recognition with RapidOCR (PaddleOCR PP-OCRv5 models on ONNX Runtime).

Detection finds every line on screen, so no capture region is needed.
"""

from __future__ import annotations

import contextlib
import re
import unicodedata
from typing import Callable

import cv2
import numpy as np
from PIL import Image

from horu.text_sources.base import TextLine
from horu.text_sources.vote import edit_distance, take_kana_sizes, vote

Box = tuple[int, int, int, int]
# Boxes tilted more than this (degrees) are straightened before reading: the recognizer only
# reads horizontal text, and slanted UI text (common in stylised menus) read through a
# straight crop comes out garbled.
_MAX_STRAIGHT_TILT = 3.0
# How much less confident a merged row may read than its separate pieces and still be kept.
_MERGE_TOLERANCE = 0.05

# The long-vowel mark ー and the kanji 一 ("one") look almost identical, and OCR mixes them
# up. After katakana, 一 is practically always ー (ラ一メン). After hiragana it is ー only
# when no kanji or つ follows: 言えね一けど is 言えねーけど, but この一つ and もう一度 are real.
_KATAKANA_THEN_ICHI = re.compile(r"(?<=[ァ-ヺ])一")
_HIRAGANA_THEN_ICHI = re.compile(r"(?<=[ぁ-ゖ])一(?![\u3400-\u9fffつ])")


# Text this small (median line height, px) is read less accurately, mostly in small game
# windows. Enlarging the frame first is available but off by default: on real screens it fixed
# some lines and broke others (evals/eval_ocr.py --upscale).
_SMALL_TEXT = 32
_TARGET_TEXT = 48
_MAX_UPSCALE = 3.0
# Lines read with less confidence than this are read again by other models and corrected by
# character-level voting. Measured on real screens (evals/eval_ocr.py): at 0.9 voting fixes
# misreads without breaking anything; at 0.95 it starts "correcting" lines that were right.
_VOTE_BELOW = 0.9
# The main model confidently misses small kana (うっかり read as うつかり), so lines with a
# full-size つ/や/ゆ/よ after kana, where a small one is plausible, are voted on too.
_SMALL_KANA_DOUBT = re.compile(r"[ぁ-ゖァ-ヺ][つやゆよツヤユヨ]")
# Games space out phrases (あ　いそがし…, ここは　ハイラル城下。) and the recognizer leaves the
# spaces out, which can change how the line splits into words (あいそ　が　し). Where the
# step between two characters is this much longer than the line's usual step, and the image
# has an empty strip at least this wide (in line heights) there, a space is put back. The
# empty strip tells a space from a character the recognizer skipped (ふしぎ[な]うでわ).
_SPACE_STEP = 1.5
_SPACE_WIDTH = 0.45


@contextlib.contextmanager
def download_progress(report: Callable[[int, int], None]):
    """While RapidOCR loads, report its model downloads (first run only) as (bytes, total
    bytes) for each file. It draws a tqdm bar for them; this stands in for the bar, and does
    nothing if RapidOCR stops using one."""
    try:
        from rapidocr.utils import download_file
    except ImportError:
        download_file = None
    if download_file is None or not hasattr(download_file, "tqdm"):
        yield
        return

    class Bar:
        def __init__(self, total=0, **kwargs) -> None:
            self.total, self.done = total or 0, 0

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> None:
            pass

        def update(self, n: int) -> None:
            self.done += n
            report(self.done, self.total)

    original = download_file.tqdm
    download_file.tqdm = Bar
    try:
        yield
    finally:
        download_file.tqdm = original


def text_finder(width: int):
    """A text detector for pictures at most `width` wide, returning boxes (left, top, right,
    bottom) of the lines it finds: the cheap half of OCR, for noticing new text."""
    from rapidocr import RapidOCR

    engine = RapidOCR(
        params={"Global.log_level": "error", "Global.use_cls": False, "Det.limit_side_len": width, "Det.limit_type": "max"}
    )

    def find(pixels: np.ndarray) -> list[Box]:
        detected = engine(pixels, use_det=True, use_cls=False, use_rec=False)
        if detected.boxes is None:
            return []
        boxes = [_box(np.asarray(b, dtype=np.float32)) for b in detected.boxes]
        return [tuple(int(v) for v in b) for b in merge_rows(boxes)]

    return find


class RapidOcrSource:
    name = "rapidocr"

    def __init__(
        self, min_score: float = 0.5, upscale: bool = False, vote: bool = True, det_params: dict | None = None
    ) -> None:
        from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR

        self._min_score = min_score
        self._upscale = upscale
        self._engine = RapidOCR(
            params={
                "Global.log_level": "error",
                "Global.use_cls": False,  # game text is never upside down
                # The v5 server model reads long lines and stylized game fonts far better
                # than the Japanese-specific v4 model.
                "Rec.ocr_version": OCRVersion.PPOCRV5,
                "Rec.model_type": ModelType.SERVER,
                **(det_params or {}),
            }
        )
        # Second-opinion readers for uncertain lines: smaller models with different mistakes.
        self._voters = []
        if vote:
            for version, lang in ((OCRVersion.PPOCRV5, None), (OCRVersion.PPOCRV4, LangRec.JAPAN)):
                params = {
                    "Global.log_level": "error",
                    "Global.use_cls": False,
                    "Rec.ocr_version": version,
                    "Rec.model_type": ModelType.MOBILE,
                }
                if lang is not None:
                    params["Rec.lang_type"] = lang
                self._voters.append(RapidOCR(params=params))
        # The first inference is several times slower than later ones; pay that cost at
        # startup rather than on the user's first hotkey press.
        blank = np.full((64, 256, 3), 255, dtype=np.uint8)
        self._engine(blank, use_det=True, use_cls=False, use_rec=False)
        for engine in [self._engine, *self._voters]:
            engine(blank, use_det=False, use_cls=False, use_rec=True)

    def read(self, image: Image.Image) -> list[TextLine]:
        image = image.convert("RGB")
        pixels = np.asarray(image)
        quads = self._detect(pixels)
        scale = 1.0
        if self._upscale and quads:
            median_height = float(np.median([_box(q)[3] - _box(q)[1] for q in quads]))
            if median_height < _SMALL_TEXT:
                scale = min(_MAX_UPSCALE, _TARGET_TEXT / max(median_height, 1))
                size = (round(image.width * scale), round(image.height * scale))
                pixels = np.asarray(image.resize(size, Image.LANCZOS))
                quads = self._detect(pixels)

        straight = [_box(q) for q in quads if abs(_tilt(q)) <= _MAX_STRAIGHT_TILT]
        tilted = [q for q in quads if abs(_tilt(q)) > _MAX_STRAIGHT_TILT]

        readings: list[tuple[str, float, list[str], Box]] = []
        # The detector sometimes splits one line into overlapping pieces (often at 、),
        # clipping characters at the seams, so pieces on the same row are read as one line.
        # But side-by-side items (tabs, menu entries) also sit on one row: the merged reading
        # is kept unless it's clearly less confident than reading the pieces separately
        # (merged tabs read as garbage; a merged split line reads about as well as its halves).
        for group in group_rows(straight):
            pieces = [(*self._read_box(pixels, b), b) for b in group]
            if len(group) > 1:
                merged_box = _union(group)
                text, score, alternatives = self._read_box(pixels, merged_box)
                confident = score >= min(p[1] for p in pieces) - _MERGE_TOLERANCE
                if _gapped(group):
                    # Pieces with a clear gap between them (a spaced-out line) lost nothing at
                    # the seams, and reading across the gap can invent a character there
                    # (体力を　回復 read as 体力を満回復), so their own readings are joined instead.
                    # A merged reading identical to the joined pieces also confirms they are
                    # one line, even if a little less confident (よく　きたな　Luke).
                    joined = _join_spaced(pieces)
                    if confident or _plain(text) == _plain(joined):
                        # But a kana's size is only clear beside the rest of the line (うっかり
                        # read alone is うつかり), so sizes come from the merged reading.
                        sized = take_kana_sizes(joined, text)
                        pieces = [(sized, min(p[1] for p in pieces), [joined, *_joined_alternatives(pieces)], merged_box)]
                elif confident:
                    pieces = [(text, score, alternatives, merged_box)]
            readings.extend(pieces)
        # Slanted lines get the same treatment, with pieces grouped along their own slant.
        slant_heights: dict[Box, float] = {}
        for group in _group_tilted(tilted):
            pieces = [(*self._read_crop(_straighten(pixels, q)), _box(q)) for q in group]
            if len(group) > 1:
                merged = _enclosing_quad(group)
                text, score, alternatives = self._read_crop(_straighten(pixels, merged))
                if score >= min(p[1] for p in pieces) - _MERGE_TOLERANCE:
                    pieces = [(text, score, alternatives, _box(merged))]
                    group = [merged]
            for quad in group:
                slant_heights[_box(quad)] = _quad_height(quad)
            readings.extend(pieces)

        lines = []
        for text, score, alternatives, box in readings:
            if text and score >= self._min_score:
                left, top, right, bottom = (round(v / scale) for v in box)
                others = tuple(dict.fromkeys(a for a in alternatives if a and _plain(a) != _plain(text)))
                slant = slant_heights.get(box, 0.0) / scale
                lines.append(TextLine(text, (left, top, right, bottom), score, others, slant))
        return lines

    def _detect(self, pixels: np.ndarray) -> list[np.ndarray]:
        """Text regions as four-corner polygons (clockwise from top-left)."""
        detected = self._engine(pixels, use_det=True, use_cls=False, use_rec=False)
        if detected.boxes is None:
            return []
        return [np.asarray(b, dtype=np.float32) for b in detected.boxes]

    def _read_box(self, pixels: np.ndarray, box: Box) -> tuple[str, float, list[str]]:
        """Read a straight line. The detector sometimes leaves a line's first character
        outside its box (the も of もし　ニワトリ), so the line is read again with more room
        on the left; that reading is taken only if it just adds characters at the start.
        (Always cropping wider garbles other lines: 大ダメージ read as 大ージ.)"""
        text, score, alternatives = self._read_crop(_crop(pixels, box))
        if not text:
            return text, score, alternatives
        wide_text, wide_score = self._recognize(np.ascontiguousarray(_crop(pixels, box, left=0.5)), spaced=True)
        plain, wide_plain = _plain(text), _plain(wide_text)
        if len(wide_plain) > len(plain) and wide_plain.endswith(plain) and wide_score >= score - _MERGE_TOLERANCE:
            added = wide_plain[: len(wide_plain) - len(plain)]
            return wide_text, wide_score, [added + a for a in alternatives]
        return text, score, alternatives

    def _read_crop(self, crop: np.ndarray) -> tuple[str, float, list[str]]:
        """Read a line, correcting an uncertain reading by voting between models. Also returns
        the readings that disagreed with the result."""
        crop = np.ascontiguousarray(crop)
        text, score = self._recognize(crop, spaced=True)
        if self._voters and text and (score < _VOTE_BELOW or _SMALL_KANA_DOUBT.search(text)):
            # The other models vote on the characters; the spaces stay where they were.
            plain = text.replace("　", "")
            voted, others = self._vote(crop, plain)
            if len(voted) == len(plain):
                voted = _respace(voted, text)
            return voted, score, [r for r in (text, *others) if _plain(r) != _plain(voted)]
        return text, score, []

    def _recognize(self, crop: np.ndarray, engine=None, spaced: bool = False) -> tuple[str, float]:
        result = (engine or self._engine)(
            crop, use_det=False, use_cls=False, use_rec=True, return_word_box=spaced, return_single_char_box=spaced
        )
        if not result.txts:
            return "", 0.0
        text = "".join(result.txts)
        if spaced and len(result.txts) == 1 and result.word_results and result.word_results[0] is not None:
            text = _with_spaces(crop, text, result.word_results[0])
        return fix_misreads(text), float(min(result.scores))

    def _vote(self, crop: np.ndarray, text: str) -> tuple[str, list[str]]:
        """Correct an uncertain reading where the other models agree on a change. Returns the
        result and the other models' readings (those close enough to count)."""
        others = []
        crop = _voting_crop(crop)
        for engine in self._voters:
            other, _ = self._recognize(crop, engine)
            # A reading far from this one is a different guess, not a correction: it abstains.
            close = edit_distance(other, text) <= max(2, len(text) * 0.4)
            others.append(other if close else text)
        return vote(text, others), others


def _with_spaces(crop: np.ndarray, text: str, info) -> str:
    """`text` with a full-width space wherever there's a clear empty gap between characters.
    `info` is the recognizer's character positions (output columns across the crop)."""
    chars = [c for word in info.words for c in word]
    columns = [c for word in info.word_cols for c in word]
    if "".join(chars) != text or len(chars) < 3 or not info.line_txt_len:
        return text
    steps = np.diff(columns)
    usual = float(np.median(steps))
    if usual <= 0:
        return text
    per_column = crop.shape[1] / info.line_txt_len
    ink = _ink_columns(crop)
    # The crop has a margin of about a tenth of the line height above and below.
    min_gap = _SPACE_WIDTH * crop.shape[0] / 1.2
    spaced = [chars[0]]
    for i, step in enumerate(steps):
        before, after = chars[i], chars[i + 1]
        if step >= _SPACE_STEP * usual and not (before.isascii() and after.isascii()) and " " not in (before, after):
            start = int((columns[i] + 0.5) * per_column)
            end = int((columns[i + 1] + 0.5) * per_column)
            if _longest_run(~ink[start:end]) >= min_gap:
                spaced.append("　")
        spaced.append(after)
    return "".join(spaced)


def _ink_columns(crop: np.ndarray) -> np.ndarray:
    """Which columns of a line's crop have text in them. Text is the smaller of the two
    brightness classes (light text on a dark box, or dark text on a light one)."""
    value = np.ascontiguousarray(crop.max(axis=2))
    _, mask = cv2.threshold(value, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    ink = mask > 0
    if ink.mean() > 0.5:
        ink = ~ink
    return ink.sum(axis=0) >= 2


def _longest_run(flags: np.ndarray) -> int:
    best = run = 0
    for flag in flags.tolist():
        run = run + 1 if flag else 0
        best = max(best, run)
    return best


def _respace(text: str, spaced: str) -> str:
    """`text` with spaces where `spaced`, a reading of the same length without them, has them."""
    chars = iter(text)
    return "".join(c if c == "　" else next(chars) for c in spaced)


def _join_spaced(pieces: list[tuple[str, float, list[str], Box]]) -> str:
    """Pieces of a spaced-out row joined, with the spaces between them put back."""
    texts = [p[0].strip("　") for p in pieces]
    return "　".join(t for t in texts if t)


def _joined_alternatives(pieces: list[tuple[str, float, list[str], Box]]) -> list[str]:
    """The joined line with one piece swapped for each of its alternative readings."""
    texts = [p[0] for p in pieces]
    return ["".join(texts[:i] + [alt] + texts[i + 1 :]) for i, p in enumerate(pieces) for alt in p[2]]


def _crop(pixels: np.ndarray, box: Box, left: float = 0.0) -> np.ndarray:
    """The box with a margin; `left` widens it on the left by that fraction of its height."""
    left_edge, top, right, bottom = box
    height = bottom - top
    pad = max(2, height // 10)
    left = left_edge - max(pad, int(height * left))
    # Extra room on the right: detection boxes tend to stop short of a small 、 or 。 at the
    # end of a line.
    return pixels[max(top - pad, 0) : bottom + pad, max(left, 0) : right + max(pad, height // 2)]


def _voting_crop(crop: np.ndarray) -> np.ndarray:
    """The crop the second-opinion readers see: a little extra room on the left. (The main
    crop is already tight above and below, so they don't pick up a neighbouring line.)"""
    margin = crop.shape[0] // 4
    return np.ascontiguousarray(np.pad(crop, ((0, 0), (margin, 0), (0, 0)), mode="edge"))


def _straighten(pixels: np.ndarray, quad: np.ndarray) -> np.ndarray:
    """Cut a tilted text region out and rotate it level, with a little margin around it."""
    top_left, top_right, bottom_right, bottom_left = quad
    width = max(np.linalg.norm(top_right - top_left), np.linalg.norm(bottom_right - bottom_left))
    height = max(np.linalg.norm(bottom_left - top_left), np.linalg.norm(bottom_right - top_right))
    # More room at the ends than above and below: the detector's polygon can clip the first
    # or last character of a slanted line.
    margin_y = max(2.0, height / 10)
    margin_x = max(2.0, height / 2)
    target = np.float32(
        [[margin_x, margin_y], [width + margin_x, margin_y], [width + margin_x, height + margin_y], [margin_x, height + margin_y]]
    )
    size = (int(width + 2 * margin_x), int(height + 2 * margin_y))
    transform = cv2.getPerspectiveTransform(quad.astype(np.float32), target)
    return cv2.warpPerspective(pixels, transform, size, borderMode=cv2.BORDER_REPLICATE)


def _quad_height(quad: np.ndarray) -> float:
    """The height of a (possibly slanted) region, measured across its slant."""
    top_left, top_right, bottom_right, bottom_left = quad
    return float(max(np.linalg.norm(bottom_left - top_left), np.linalg.norm(bottom_right - top_right)))


def _box(quad: np.ndarray) -> Box:
    return (int(quad[:, 0].min()), int(quad[:, 1].min()), int(quad[:, 0].max()), int(quad[:, 1].max()))


def _tilt(quad: np.ndarray) -> float:
    """Angle of the region's top edge, in degrees."""
    (x0, y0), (x1, y1) = quad[0], quad[1]
    return float(np.degrees(np.arctan2(y1 - y0, x1 - x0)))


def _group_tilted(quads: list[np.ndarray]) -> list[list[np.ndarray]]:
    """Group slanted pieces that lie on the same slanted line: similar tilt, and on the same
    row once rotated level."""
    groups: list[list[np.ndarray]] = []
    for quad in sorted(quads, key=lambda q: float(q[:, 0].min())):
        for group in groups:
            angle = _tilt(group[0])
            if abs(_tilt(quad) - angle) <= 4 and _same_row(_level_box(_enclosing_quad(group), angle), _level_box(quad, angle)):
                group.append(quad)
                break
        else:
            groups.append([quad])
    return groups


def _level_box(quad: np.ndarray, angle: float) -> Box:
    """The axis-aligned box of `quad` after rotating everything by -angle degrees."""
    theta = np.radians(-angle)
    rotation = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    return _box(quad @ rotation.T)


def _enclosing_quad(quads: list[np.ndarray]) -> np.ndarray:
    """The smallest rotated rectangle around several pieces, as corners clockwise from the
    top-left (the order _straighten expects)."""
    corners = cv2.boxPoints(cv2.minAreaRect(np.concatenate(quads).astype(np.float32)))
    # Order: top-left has the smallest x+y, bottom-right the largest; top-right the smallest y-x.
    total, diff = corners.sum(axis=1), np.diff(corners, axis=1).ravel()
    return np.float32([corners[np.argmin(total)], corners[np.argmin(diff)], corners[np.argmax(total)], corners[np.argmax(diff)]])


def _union(boxes: list[Box]) -> Box:
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


# The recognition model is trained mostly on Chinese, so it sometimes returns the simplified
# (or traditional) Chinese form of a kanji (黑服 for 黒服, 决定 for 決定, 攻擊 for 攻撃). These
# forms aren't used in modern Japanese, so mapping them back is safe.
_SIMPLIFIED_TO_JAPANESE = str.maketrans(
    "黑决说读话语门问间闻开关时东车长马鸟鱼见觉发对应实战击损伤气钱买卖过还进远边让识认请谁这样"
    "们个来动头脸题颜风飞饭馆机杀杂权欢欧尔导将层岁币币师带帮广庆应张强归录彻忆态总恋恶惊惯擊",
    "黒決説読話語門問間聞開関時東車長馬鳥魚見覚発対応実戦撃損傷気銭買売過還進遠辺譲識認請誰這様"
    "們個来動頭顔題顔風飛飯館機殺雑権歓欧爾導将層歳幣幣師帯幇広慶応張強帰録徹憶態総恋悪驚慣撃",
)


def fix_misreads(text: str) -> str:
    text = text.translate(_SIMPLIFIED_TO_JAPANESE)
    return _HIRAGANA_THEN_ICHI.sub("ー", _KATAKANA_THEN_ICHI.sub("ー", text))


def group_rows(boxes: list[Box]) -> list[list[Box]]:
    """Group boxes that sit on the same text row and touch or nearly touch horizontally."""
    groups: list[list[Box]] = []
    for box in sorted(boxes, key=lambda b: b[0]):
        for group in groups:
            if _same_row(_union(group), box):
                group.append(box)
                break
        else:
            groups.append([box])
    return groups


def merge_rows(boxes: list[Box]) -> list[Box]:
    """Boxes on the same text row merged into one."""
    return [_union(group) for group in group_rows(boxes)]


def _gapped(group: list[Box]) -> bool:
    """Every piece is set apart from the next by at least a narrow space."""
    ordered = sorted(group, key=lambda b: b[0])
    return all(
        b[0] - a[2] >= 0.3 * min(a[3] - a[1], b[3] - b[1]) for a, b in zip(ordered, ordered[1:])
    )


def _same_row(a: Box, b: Box) -> bool:
    height = min(a[3] - a[1], b[3] - b[1])
    vertical_overlap = min(a[3], b[3]) - max(a[1], b[1])
    gap = max(a[0], b[0]) - min(a[2], b[2])  # negative when they overlap
    # Pieces of one line sit side by side; boxes that overlap mostly horizontally are stacked
    # (a label under a heading), not pieces of the same line.
    narrower = min(a[2] - a[0], b[2] - b[0])
    stacked = -gap > 0.5 * narrower
    # A space is as wide as the text before it, so the allowed gap is measured against the
    # left box: a short Latin name set a wide space after large Japanese text (よく　きたな
    # Luke) is on the same line, a button beside a small label (日本語音声　ON) is not.
    before = a if a[0] <= b[0] else b
    return vertical_overlap >= 0.6 * height and gap <= 0.8 * max(height, before[3] - before[1]) and not stacked


def _plain(text: str) -> str:
    """Text without spacing or width differences (！ vs !), which don't change what it says."""
    return re.sub(r"\s", "", unicodedata.normalize("NFKC", text))
