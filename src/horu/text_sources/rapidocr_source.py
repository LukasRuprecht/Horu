"""Text detection + recognition with RapidOCR (PaddleOCR PP-OCRv5 models on ONNX Runtime).

Detection finds every line on screen, so no capture region is needed.
"""

from __future__ import annotations

import re

import numpy as np
from PIL import Image

from horu.text_sources.base import TextLine

Box = tuple[int, int, int, int]

# The long-vowel mark ー and the kanji 一 ("one") look almost identical, and OCR mixes them
# up. After katakana, 一 is practically always ー (ラ一メン). After hiragana it is ー only
# when no kanji or つ follows: 言えね一けど is 言えねーけど, but この一つ and もう一度 are real.
_KATAKANA_THEN_ICHI = re.compile(r"(?<=[ァ-ヺ])一")
_HIRAGANA_THEN_ICHI = re.compile(r"(?<=[ぁ-ゖ])一(?![\u3400-\u9fffつ])")


class RapidOcrSource:
    name = "rapidocr"

    def __init__(self, min_score: float = 0.5) -> None:
        from rapidocr import ModelType, OCRVersion, RapidOCR

        self._min_score = min_score
        self._engine = RapidOCR(
            params={
                "Global.log_level": "error",
                "Global.use_cls": False,  # game text is never upside down
                # The v5 server model reads long lines and stylized game fonts far better
                # than the Japanese-specific v4 model.
                "Rec.ocr_version": OCRVersion.PPOCRV5,
                "Rec.model_type": ModelType.SERVER,
            }
        )
        # The first inference is several times slower than later ones; pay that cost at
        # startup rather than on the user's first hotkey press.
        blank = np.full((64, 256, 3), 255, dtype=np.uint8)
        self._engine(blank, use_det=True, use_cls=False, use_rec=False)
        self._engine(blank, use_det=False, use_cls=False, use_rec=True)

    def read(self, image: Image.Image) -> list[TextLine]:
        pixels = np.asarray(image.convert("RGB"))
        detected = self._engine(pixels, use_det=True, use_cls=False, use_rec=False)
        if detected.boxes is None:
            return []
        boxes = [
            (int(b[:, 0].min()), int(b[:, 1].min()), int(b[:, 0].max()), int(b[:, 1].max())) for b in detected.boxes
        ]

        # The detector sometimes splits one line into overlapping pieces (often at 、),
        # clipping characters at the seams. Merge pieces on the same row and read each
        # full line in one pass instead.
        lines = []
        for left, top, right, bottom in merge_rows(boxes):
            pad = max(2, (bottom - top) // 10)
            # Extra room on the right: detection boxes tend to stop short of a small 、 or 。
            # at the end of a line.
            crop = pixels[max(top - pad, 0) : bottom + pad, max(left - pad, 0) : right + (bottom - top) // 2]
            result = self._engine(crop, use_det=False, use_cls=False, use_rec=True)
            if not result.txts:
                continue
            text, score = fix_misreads("".join(result.txts)), float(min(result.scores))
            if text and score >= self._min_score:
                lines.append(TextLine(text, (left, top, right, bottom), score))
        return lines


# The recognition model is trained mostly on Chinese, so it sometimes returns the simplified
# Chinese form of a kanji (黑服 for 黒服, 决定 for 決定). These forms aren't used in Japanese,
# so mapping them back is safe.
_SIMPLIFIED_TO_JAPANESE = str.maketrans(
    "黑决说读话语门问间闻开关时东车长马鸟鱼见觉发对应实战击损伤气钱买卖过还进远边让识认请谁这样"
    "们个来动头脸题颜风飞饭馆机杀杂权欢欧尔导将层岁币币师带帮广庆应张强归录彻忆态总恋恶惊惯",
    "黒決説読話語門問間聞開関時東車長馬鳥魚見覚発対応実戦撃損傷気銭買売過還進遠辺譲識認請誰這様"
    "們個来動頭顔題顔風飛飯館機殺雑権歓欧爾導将層歳幣幣師帯幇広慶応張強帰録徹憶態総恋悪驚慣",
)


def fix_misreads(text: str) -> str:
    text = text.translate(_SIMPLIFIED_TO_JAPANESE)
    return _HIRAGANA_THEN_ICHI.sub("ー", _KATAKANA_THEN_ICHI.sub("ー", text))


def merge_rows(boxes: list[Box]) -> list[Box]:
    """Merge boxes that sit on the same text row and touch or nearly touch horizontally."""
    merged: list[Box] = []
    for box in sorted(boxes, key=lambda b: b[0]):
        for i, other in enumerate(merged):
            if _same_row(other, box):
                merged[i] = (min(other[0], box[0]), min(other[1], box[1]), max(other[2], box[2]), max(other[3], box[3]))
                break
        else:
            merged.append(box)
    return merged


def _same_row(a: Box, b: Box) -> bool:
    height = min(a[3] - a[1], b[3] - b[1])
    vertical_overlap = min(a[3], b[3]) - max(a[1], b[1])
    gap = max(a[0], b[0]) - min(a[2], b[2])  # negative when they overlap
    return vertical_overlap >= 0.6 * height and gap <= 0.8 * height
