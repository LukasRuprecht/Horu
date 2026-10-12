from horu.app import _pieces_of, _same_line


def test_a_line_read_again_with_ocr_noise_is_a_repeat():
    assert _same_line("向こう1年は、大人しく暮らせ。何も起こさなきゃ…観察も解ける。", "向こう１年は、大人しく喜らせ。何も起こさなきゃ…観察も解ける。")
    assert _same_line("1年か…", "１年か…")
    assert _same_line("あ　いそがしい", "あいそがしい")


def test_different_lines_are_not():
    assert not _same_line("はい", "ない")  # short lines must match exactly
    assert not _same_line("…観察？", "1年か…")
    assert not _same_line("向こう1年は、大人しく暮らせ。", "向こう2年は、大人しく暮らすな。")


def test_a_line_read_in_pieces_and_then_whole_is_a_repeat():
    # P5R: the mouse pointer over the second line split it from the first.
    shown = ["昼間", "というか…", "今度退学になったら、うちからも追い出すぞ？", "早送り"]
    assert _pieces_of("というか…今度退学になったら、うちからも追い出すぞ？", shown)
    # And the other way round: the pointer covering the end of a line shown whole.
    assert _pieces_of("今度退学になったら、うちからも追い", ["というか…今度退学になったら、うちからも追い出すぞ？"])
    # A short piece is not enough, and new text isn't a repeat.
    assert not _pieces_of("はい", ["はいはい、わかったよ。"])
    assert not _pieces_of("ルブランという店にいるらしい。", shown)
