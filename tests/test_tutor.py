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


def test_reading_replies_are_cleaned():
    from horu.tutor import clean_reading

    assert clean_reading("「マップ」") == "マップ"
    assert clean_reading("Text: 太陽は月に…\n月は太陽に…") == "太陽は月に…月は太陽に…"


def test_providers_send_the_image():
    from types import SimpleNamespace

    from horu.llm.anthropic_provider import AnthropicProvider
    from horu.llm.openai_compat import OpenAICompatProvider

    calls = []

    def claude_create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="マップ")])

    messages = SimpleNamespace(create=claude_create)
    claude = AnthropicProvider("claude-opus-5-5", client=SimpleNamespace(messages=messages, beta=SimpleNamespace(messages=messages)))
    assert claude.read_image("Read it.", b"png") == "マップ"
    image, text = calls[-1]["messages"][0]["content"]
    assert image["type"] == "image" and image["source"]["data"] == "cG5n" and text["text"] == "Read it."

    def openai_create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="マップ"))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=openai_create)))
    gemini = OpenAICompatProvider("gemini-flash", "https://example.com/v1", client=client)
    assert gemini.read_image("Read it.", b"png") == "マップ"
    text, image = calls[-1]["messages"][0]["content"]
    assert image["image_url"]["url"] == "data:image/png;base64,cG5n"
