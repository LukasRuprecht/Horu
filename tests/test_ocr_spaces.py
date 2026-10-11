from types import SimpleNamespace

import numpy as np

from horu.text_sources.rapidocr_source import _with_spaces

# A line's crop: 40 px tall, characters 22 px wide on a dark box. The recognizer reports each
# character's position as an output column, 8 px wide here.
HEIGHT, COLUMN = 40, 8


def crop_with(ink_spans: list[tuple[int, int]]) -> np.ndarray:
    crop = np.full((HEIGHT, 480, 3), 30, dtype=np.uint8)
    for left, right in ink_spans:
        crop[8:32, left:right] = 240
    return crop


def info(text: str, columns: list[int]):
    return SimpleNamespace(words=[list(text)], word_cols=[columns], line_txt_len=480 // COLUMN)


def test_a_wide_empty_gap_is_a_space():
    # あ　いそが: a character's width of empty box between あ and い.
    columns = [1, 7, 10, 13]
    crop = crop_with([(0, 22), (48, 70), (72, 94), (96, 118)])
    assert _with_spaces(crop, "あいそが", info("あいそが", columns)) == "あ　いそが"


def test_a_gap_with_ink_is_a_missed_character_not_a_space():
    # ふしぎ[な]うでわ: the recognizer skipped な, but it's there in the image.
    columns = [1, 4, 7, 13, 16]
    crop = crop_with([(0, 22), (24, 46), (48, 70), (72, 94), (96, 118), (120, 142)])
    assert _with_spaces(crop, "ふしぎうで", info("ふしぎうで", columns)) == "ふしぎうで"


def test_evenly_spaced_text_is_left_alone():
    columns = [1, 4, 7, 10]
    crop = crop_with([(0, 22), (24, 46), (48, 70), (72, 94)])
    assert _with_spaces(crop, "ここは。", info("ここは。", columns)) == "ここは。"
