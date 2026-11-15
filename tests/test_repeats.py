from horu.app import _pieces_of, _same_line, _types_on


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


def test_button_icons_and_one_misread_character_dont_make_a_line_new():
    # Ocarina of Time: Ⅰ・Ⓧ・Ⓨ・Ⅱにセットして使う, read differently on each auto read.
    assert _same_line("・Ⅱ にセットして使う", "・にセットして使う")
    assert _same_line("・皿にセットして使う", "・II にセットして使う")
    assert _same_line("ディンの炎を　手に入れた！", "ディンの炎を一手に入れた！")
    # Different lines still are.
    assert not _same_line("炎のドームの　攻撃魔法！", "ディンの炎を　手に入れた！")
    assert _same_line("インの炎を　手に入れた！", "ディンの炎を　手に入れた！")  # ディ read as イ


def test_a_line_typed_out_further_extends_the_earlier_read():
    assert _types_on("おい、そこのあんた。", "おい、そこのあんた。なにしに来た？")
    # Paused at an ellipsis, its dots and bracket are read differently once more follows (Yakuza).
    assert _types_on("(チャンピオン街…", "（チャンピオン街……　ここに立華不動産の人間が出入りしているらしいが")
    # One character of the shared part read differently.
    assert _types_on("俺らはもうヤクザにウンザリなんだよ！　お前らは", "俺らはもうヤクザにウンザリなんだよ！!お前らはここらの店をゴミとしか")


def test_another_line_doesnt_extend_the_earlier_one():
    assert not _types_on("あんた、また東城会の若い衆か？", "いや、そうじゃない。あんたらの店を地上げしにきたわけでもない。")
    assert not _types_on("そうか", "そうだ、思い出した。")  # too short to allow for a misread
    assert not _types_on("留守みたいだな…", "留守みたいだな…")  # the same line: a repeat, not more of it
    assert not _types_on("…", "…なんだよ。")  # nothing but dots to go by

