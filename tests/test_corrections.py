from horu.corrections import Corrections, Hints
from horu.layout import group_blocks
from horu.text_sources.base import TextLine


def line(text, left, top, right, bottom):
    return TextLine(text, (left, top, right, bottom))


def test_rules_persist_per_game(tmp_path):
    path = tmp_path / "corrections.json"
    c = Corrections(path)
    c.mark("P5R.exe", "音声 再生", "dialogue")
    c.mark("P5R.exe", "ジョーカー", "speaker")
    c.mark("P3R.exe", "ログ", "other")
    reloaded = Corrections(path)
    assert reloaded.hints("P5R.exe") == Hints(dialogue=frozenset({"音声再生"}), speaker=frozenset({"ジョーカー"}))
    assert reloaded.hints("P3R.exe").other == {"ログ"}
    reloaded.mark("P5R.exe", "音声再生", None)
    assert Corrections(path).hints("P5R.exe").dialogue == frozenset()


def test_hints_override_the_automatic_rules():
    lines = [
        line("ジョーカー", 100, 100, 220, 130),  # same size and style as the line below
        line("行こう、みんな。", 100, 140, 400, 170),
        line("音声再生", 100, 600, 200, 630),  # a UI word, normally "other"
        line("少女の声", 100, 300, 220, 330),  # name-like, normally a speaker
        line("ん…？", 100, 340, 180, 370),
    ]
    automatic = group_blocks(lines)
    assert [(b.speaker, b.kind) for b in automatic] == [
        (None, "other"), (None, "dialogue"), ("少女の声", "dialogue"), (None, "other"),
    ]

    hints = Hints(speaker=frozenset({"ジョーカー"}), dialogue=frozenset({"音声再生"}), not_speaker=frozenset({"少女の声"}))
    corrected = group_blocks(lines, hints=hints)
    assert [(b.speaker, b.text, b.kind) for b in corrected] == [
        ("ジョーカー", "行こう、みんな。", "dialogue"),
        (None, "少女の声", "other"),
        (None, "ん…？", "dialogue"),
        (None, "音声再生", "dialogue"),
    ]
