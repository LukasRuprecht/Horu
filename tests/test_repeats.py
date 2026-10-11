from horu.app import _same_line


def test_a_line_read_again_with_ocr_noise_is_a_repeat():
    assert _same_line("向こう1年は、大人しく暮らせ。何も起こさなきゃ…観察も解ける。", "向こう１年は、大人しく喜らせ。何も起こさなきゃ…観察も解ける。")
    assert _same_line("1年か…", "１年か…")
    assert _same_line("あ　いそがしい", "あいそがしい")


def test_different_lines_are_not():
    assert not _same_line("はい", "ない")  # short lines must match exactly
    assert not _same_line("…観察？", "1年か…")
    assert not _same_line("向こう1年は、大人しく暮らせ。", "向こう2年は、大人しく暮らすな。")
