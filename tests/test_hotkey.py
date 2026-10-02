import pytest

from horu.hotkey import HotkeyError, parse_win32


def test_parse_modifiers_and_letter():
    assert parse_win32("<ctrl>+<alt>+j") == (0x2 | 0x1, ord("J"))


def test_parse_function_key():
    assert parse_win32("<f9>") == (0, 0x78)


@pytest.mark.parametrize("spec", ["<ctrl>", "<ctrl>+a+b", "<ctrl>+<nope>"])
def test_parse_rejects_bad_specs(spec):
    with pytest.raises(HotkeyError):
        parse_win32(spec)
