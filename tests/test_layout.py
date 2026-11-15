import pytest

from horu.layout import Style, group_blocks, is_legend
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


def test_stacked_boxes_are_not_one_row():
    from horu.text_sources.rapidocr_source import merge_rows

    # "ACTION!" with "ジャンプ" just below, overlapping its lower half: a label, not a split line.
    assert len(merge_rows([(445, 601, 642, 689), (563, 651, 671, 702)])) == 2


def test_spaced_out_pieces_are_read_separately():
    from horu.text_sources.rapidocr_source import _gapped

    # Zelda's 体力を　回復することができる。: one row, but a full-width space between the
    # pieces. Reading across the gap invented a kanji there.
    assert _gapped([(981, 378, 1944, 510), (606, 380, 909, 509)])
    # A line the detector split into overlapping pieces is still read as one crop.
    assert not _gapped([(100, 50, 400, 100), (390, 52, 700, 100)])


def test_tilted_pieces_on_one_slanted_line_are_grouped():
    import numpy as np

    from horu.text_sources.rapidocr_source import _enclosing_quad, _group_tilted, _tilt

    def tilted(x, y, w, h, angle=-10):
        theta = np.radians(angle)
        d = np.array([np.cos(theta), np.sin(theta)])
        n = np.array([-np.sin(theta), np.cos(theta)])
        origin = np.array([x, y], dtype=float)
        return np.float32([origin, origin + w * d, origin + w * d + h * n, origin + h * n])

    left = tilted(100, 300, 60, 20)
    right = tilted(100 + 62 * np.cos(np.radians(-10)), 300 + 62 * np.sin(np.radians(-10)), 80, 20)
    other_line = tilted(100, 340, 140, 20)
    groups = _group_tilted([left, right, other_line])
    assert sorted(len(g) for g in groups) == [1, 2]
    merged = _enclosing_quad(next(g for g in groups if len(g) == 2))
    assert abs(_tilt(merged) - -10) < 1


def test_centred_dialogue_with_uneven_lines_is_one_block():
    # Zelda's owl: centred lines of any length, the first short and the last running past.
    owl = [
        line("よくきたなLuke", 856, 900, 1596, 1020),
        line("ここが聖なる山", 934, 1034, 1610, 1155),
        line("デスマウンテンの山頂じゃ。", 754, 1170, 1745, 1292),
    ]
    [block] = group_blocks(owl)
    assert block.kind == "dialogue" and len(block.lines) == 3
    # A centred menu doesn't end in sentence punctuation, so its items stay apart.
    menu = [line("はじめから", 900, 600, 1100, 660), line("つづきから", 900, 670, 1100, 730)]
    assert len(group_blocks(menu)) == 2


def test_a_short_name_after_a_wide_space_is_on_the_same_row():
    assert merge_rows([(856, 900, 1380, 1020), (1445, 924, 1596, 1000)]) == [(856, 900, 1596, 1020)]
    # A button beside a small label is not.
    assert len(merge_rows([(739, 165, 854, 192), (876, 158, 952, 205)])) == 2


def test_rows_set_apart_for_furigana_still_join():
    # Ocarina of Time 3D: the furigana over 大金持ち widens the gap between the rows.
    lines = [
        line("のろいを解いていただけるなら", 151, 427, 581, 462),
        line("大金持ちにしてさしあげます。", 176, 475, 556, 509),
        line("おおがねも", 177, 458, 269, 479),
    ]
    assert [b.text for b in group_blocks(lines)] == ["のろいを解いていただけるなら大金持ちにしてさしあげます。"]


def test_misread_furigana_is_not_a_speaker():
    # Ocarina of Time 3D: the furigana まほう over 魔法 read as 手ほう.
    lines = [
        line("魔法のツボを手に入れた！", 111, 73, 489, 114),
        line("魔力のゲージが回復する！", 116, 119, 482, 160),
        line("まりよく", 122, 108, 176, 127),
        line("手ほう", 123, 61, 170, 81),
        line("かいく", 350, 108, 407, 127),
    ]
    blocks = group_blocks(lines)
    assert [(b.speaker, b.text) for b in blocks] == [(None, "魔法のツボを手に入れた！魔力のゲージが回復する！")]


def test_centred_sign_title_is_not_a_speaker():
    # Ocarina of Time 3D: a sign's place name, centred over its text.
    lines = [
        line("ゾーラ川", 307, 419, 424, 465),
        line("急流に注意！トコトン流されます", 142, 473, 588, 508),
    ]
    blocks = group_blocks(lines)
    assert all(b.speaker is None for b in blocks)
    assert "急流に注意！トコトン流されます" in [b.text for b in blocks]


def test_title_in_its_own_colour_joins_the_text_below():
    lines = [
        line("ゾーラ川", 222, 392, 308, 426),
        line("急流に注意!トコトン流されます", 102, 430, 426, 457),
    ]
    blue, white = Style((60, 90, 220), True), Style((235, 235, 235), True)
    [block] = group_blocks(lines, styles=[blue, white])
    assert (block.kind, block.speaker, block.text) == ("dialogue", None, "ゾーラ川　急流に注意!トコトン流されます")


def test_centred_menu_in_one_colour_stays_apart():
    lines = [line("はじめから", 200, 300, 330, 330), line("つづきから", 200, 340, 330, 370)]
    white = Style((235, 235, 235), True)
    assert len(group_blocks(lines, styles=[white, white])) == 2


@pytest.mark.parametrize(
    "tag, tag_box, body",
    [
        # P5R: the name tag is slanted and as tall as the text; its box is much taller.
        ("愛想のない警察団", (197, 413, 350, 479), [("奥の路地だよ。", 278, 483, 392, 503), ("第三ビルの手前を曲がった先。", 277, 503, 514, 525)]),
        ("愛想のない番察官", (50, 429, 203, 488), [("まだ何か？", 136, 491, 223, 515)]),
    ],
)
def test_a_slanted_name_tag_is_a_speaker(tag, tag_box, body):
    white = Style((245, 245, 245), True)
    lines = [TextLine(tag, tag_box, 0.8, slant_height=30.7), *(line(*b) for b in body)]
    [block] = group_blocks(lines, styles=[white] * len(lines))
    assert block.speaker == tag and block.speaker_line.box == tag_box


def test_a_name_tag_up_and_left_of_the_text_is_its_speaker():
    # Yakuza: the tag sits above the box's left corner, the text starts further in. A line
    # opening with a bracket starts further still: over 3 line heights from the tag's left edge.
    tag = line("桐生", 243, 668, 303, 705)
    blocks = group_blocks([tag, line("（何を渡そうか……?）", 372, 711, 674, 753)])
    assert [(b.speaker, b.text) for b in blocks] == [("桐生", "（何を渡そうか……?）")]
    # Text beside the tag rather than below it isn't what the name says.
    blocks = group_blocks([line("魔法のマメ", 131, 611, 330, 657), line("なにかが起こる？", 360, 649, 600, 682)])
    assert [b.speaker for b in blocks] == [None, None]


def test_lines_under_a_name_tag_are_one_speech_however_the_first_ends():
    # Yakuza: a short first line whose 。 was read as a kanji, then a longer line. Without a
    # name tag that's a label over another list item.
    said = [line("あ、桐生さん所", 337, 710, 575, 758), line("ちょっと職務質問お願いします！", 339, 745, 823, 788)]
    blocks = group_blocks([line("菊池", 242, 668, 304, 705), *said])
    assert [(b.speaker, b.text) for b in blocks] == [("菊池", "あ、桐生さん所ちょっと職務質問お願いします！")]
    assert len(group_blocks(said)) == 2

