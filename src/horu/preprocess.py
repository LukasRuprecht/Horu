"""Image cleanup before OCR."""

from __future__ import annotations

import numpy as np
from PIL import Image


def crop_to_text(image: Image.Image, threshold: int = 60, pad: int = 16) -> Image.Image:
    """Crop away the empty part of a dialogue box.

    manga-ocr resizes its input to a small square, so a wide box with text in one corner
    gets squashed and misread. The background colour is estimated from the image border,
    and anything that differs from it by more than `threshold` is treated as text.
    """
    px = np.asarray(image.convert("RGB"), dtype=np.int16)
    border = np.concatenate([px[0], px[-1], px[:, 0], px[:, -1]])
    background = np.median(border, axis=0)

    mask = np.abs(px - background).max(axis=2) > threshold
    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    if rows.size == 0:
        return image

    h, w = mask.shape
    return image.crop(
        (
            max(int(cols[0]) - pad, 0),
            max(int(rows[0]) - pad, 0),
            min(int(cols[-1]) + 1 + pad, w),
            min(int(rows[-1]) + 1 + pad, h),
        )
    )
