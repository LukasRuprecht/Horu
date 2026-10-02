import pytest

from horu.layout import group_blocks, is_legend
from horu.text_sources.base import TextLine
from horu.text_sources.rapidocr_source import merge_rows


def line(text, left, top, right, bottom):
    return TextLine(text, (left, top, right, bottom))


def test_dialogue_box_with_name_tag():
    lines = [
        line("4/7火 放課後", 49, 27, 406, 100),
        line("MENU", 2192, 54, 2332, 105),
        line("順平", 326, 1020, 446, 1089),
        line("おいおい、転校初日から遅刻かよ？", 370, 1119, 1269, 1200),
        line("まあ、オレも人のこと言えねーけどな。", 372, 1208, 1361, 1289),
    ]
    blocks = group_blocks(lines)
    assert [b.text for b in blocks] == [
        "4/7火 放課後",
        "おいおい、転校初日から遅刻かよ？まあ、オレも人のこと言えねーけどな。",
    ]
    assert blocks[1].speaker == "順平"


def test_short_first_line_with_punctuation_is_not_a_speaker():
    blocks = group_blocks([line("えっ？", 100, 100, 200, 160), line("本当にそうなの？", 100, 170, 500, 230)])
    assert blocks[0].speaker is None
    assert blocks[0].text == "えっ？本当にそうなの？"


def test_lone_characters_are_dropped():
    assert group_blocks([line("日", 10, 10, 40, 40)]) == []


def test_merge_rows_joins_split_line_but_not_lines_below():
    boxes = [(370, 1119, 678, 1200), (646, 1121, 1269, 1193), (372, 1208, 1361, 1289)]
    assert merge_rows(boxes) == [(370, 1119, 1269, 1200), (372, 1208, 1361, 1289)]


def test_merge_rows_keeps_distant_boxes_apart():
    assert len(merge_rows([(0, 0, 100, 50), (400, 0, 500, 50)])) == 2


def test_fix_misreads_long_vowel_mark():
    from horu.text_sources.rapidocr_source import fix_misreads

    assert fix_misreads("言えね一けどな") == "言えねーけどな"
    assert fix_misreads("ラ一メン") == "ラーメン"
    assert fix_misreads("すご一") == "すごー"
    for real in ("この一つ", "もう一度", "その一人", "一番"):
        assert fix_misreads(real) == real


@pytest.mark.parametrize(
    "text, legend",
    [
        ("F早送り", True),
        ("区オート", True),  # boxed X key read as 区
        ("Tab メニュー", True),
        ("Tab X=ュ- snit", True),  # garbled, but starts with a key name
        ("L1 切替", True),
        ("決定", True),
        ("Tシャツ", False),  # a letter that's part of a word
        ("ロボット", False),  # starts with a box-like kana
        ("あそこだ!", False),
    ],
)
def test_button_legends(text, legend):
    assert is_legend(text) is legend


def test_short_lines_are_dialogue_only_if_they_read_like_sentences():
    kinds = {b.text: b.kind for b in group_blocks([
        line("あそこだ", 100, 100, 220, 130),  # ends in the copula
        line("エイガオン", 100, 400, 260, 430),  # a skill name
        line("そうか", 100, 700, 200, 730),  # ends in a sentence-final particle
    ])}
    assert kinds == {"あそこだ": "dialogue", "エイガオン": "other", "そうか": "dialogue"}
