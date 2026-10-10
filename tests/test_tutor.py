from horu.app import _block_alternatives
from horu.layout import TextBlock
from horu.text_sources.base import TextLine
from horu.tutor import Line


def test_block_alternatives_swap_in_one_line_at_a_time():
    block = TextBlock((
        TextLine("近くへ来て足につまりなさい。", (0, 0, 10, 10), 0.94, ("近くへ来て足につかまりなさい。",)),
        TextLine("さあ勇気を出して。", (0, 12, 10, 22)),
    ))
    assert _block_alternatives(block) == ["近くへ来て足につかまりなさい。さあ勇気を出して。"]


def test_ocr_alternatives_reach_the_prompt():
    line = Line("近くへ来て足につまりなさい。", alternatives=["近くへ来て足につかまりなさい。"])
    context = line.context()
    assert "「近くへ来て足につかまりなさい。」" in context and "misread" in context
    assert Line("そうだね").context() == ""
