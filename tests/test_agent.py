import json
from types import SimpleNamespace

import pytest

from horu import agent, anki
from horu.llm.anthropic_provider import AnthropicProvider
from horu.llm.base import AIError
from horu.llm.openai_compat import OpenAICompatProvider
from horu.llm.tools import Tool


def echo_tool(log):
    def run(args):
        log.append(args)
        if args.get("word") == "boom":
            raise ValueError("bad word")
        return f"looked up {args['word']}"

    schema = {"type": "object", "properties": {"word": {"type": "string"}}, "required": ["word"], "additionalProperties": False}
    return Tool("lookup_word", "Look a word up.", schema, run)


# --- Claude ------------------------------------------------------------------------------


def block(type_, **fields):
    return SimpleNamespace(type=type_, **fields)


class FakeClaude:
    def __init__(self, *responses):
        self.calls = []
        self._responses = list(responses)
        messages = SimpleNamespace(create=self._create)
        self.messages = messages
        self.beta = SimpleNamespace(messages=messages)

    def _create(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return self._responses.pop(0)


def test_claude_tool_loop_runs_tools_and_returns_final_text():
    log = []
    tool_turn = SimpleNamespace(stop_reason="tool_use", content=[
        block("text", text="Let me look these up."),
        block("tool_use", id="t1", name="lookup_word", input={"word": "心配"}),
        block("tool_use", id="t2", name="lookup_word", input={"word": "boom"}),
    ])
    client = FakeClaude(tool_turn, SimpleNamespace(stop_reason="end_turn", content=[block("text", text="Added 心配.")]))
    provider = AnthropicProvider("claude-opus-5-5", client=client)
    assert provider.run_tools("SYSTEM", "make cards", [echo_tool(log)]) == "Added 心配."

    first, second = client.calls
    assert first["tools"][0]["strict"] is True and first["tools"][0]["input_schema"]["additionalProperties"] is False
    assert first["fallbacks"] == "default" and "tool_choice" not in first
    assert log == [{"word": "心配"}, {"word": "boom"}]
    # Both results go back in one user message, matched by id; the failure is flagged.
    assistant, results = second["messages"][1], second["messages"][2]
    # The response goes back unchanged (append-only history).
    assert assistant == {"role": "assistant", "content": tool_turn.content}
    assert results["role"] == "user"
    assert [r["tool_use_id"] for r in results["content"]] == ["t1", "t2"]
    assert results["content"][0] == {"type": "tool_result", "tool_use_id": "t1", "content": "looked up 心配"}
    assert results["content"][1]["is_error"] is True and "bad word" in results["content"][1]["content"]


def test_claude_tool_loop_stops_after_too_many_turns():
    looping = SimpleNamespace(stop_reason="tool_use", content=[block("tool_use", id="t", name="lookup_word", input={"word": "x"})])
    provider = AnthropicProvider("claude-opus-5-5", client=FakeClaude(*[looping] * 3))
    with pytest.raises(AIError, match="more steps"):
        provider.run_tools("S", "task", [echo_tool([])], max_turns=3)


# --- OpenAI-compatible -------------------------------------------------------------------


def openai_response(content=None, calls=()):
    tool_calls = [
        SimpleNamespace(id=i, function=SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False)))
        for i, name, args in calls
    ]
    message = SimpleNamespace(content=content, tool_calls=tool_calls or None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeOpenAI:
    def __init__(self, *responses):
        self.calls = []
        self._responses = list(responses)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return self._responses.pop(0)


def test_openai_compat_tool_loop():
    log = []
    client = FakeOpenAI(
        openai_response(calls=[("c1", "lookup_word", {"word": "心配"})]),
        openai_response(content="Added 心配."),
    )
    provider = OpenAICompatProvider("qwen3:8b", "http://127.0.0.1:11434/v1", client=client)
    assert provider.run_tools("SYSTEM", "make cards", [echo_tool(log)]) == "Added 心配."
    first, second = client.calls
    assert first["tools"][0]["function"]["name"] == "lookup_word"
    assert second["messages"][-1] == {"role": "tool", "tool_call_id": "c1", "content": "looked up 心配"}
    assert second["messages"][-2]["tool_calls"][0]["id"] == "c1"


# --- The card tools ----------------------------------------------------------------------


class FakeDeck:
    def __init__(self, existing=()):
        self.words = set(existing)
        self.added = []

    def has_word(self, deck, word):
        return word in self.words

    def add_card(self, deck, card, screenshot):
        if card.dictionary_form in self.words:
            raise anki.AnkiError(f"{card.dictionary_form} is already in your {deck} deck.")
        self.words.add(card.dictionary_form)
        self.added.append((deck, card, screenshot))
        return len(self.added)


def fake_app(deck):
    line = {
        "text": "とにかく、こっちの事は心配無用だ。",
        "speaker": {"text": "少年のような声"},
        "game": "P5R.exe",
        "words": [
            {"surface": "心配", "dictionary_form": "心配", "dictionary_reading": "しんぱい", "grammar": []},
            {"surface": "無用", "dictionary_form": "無用", "dictionary_reading": "むよう", "grammar": []},
        ],
    }
    app = SimpleNamespace(lines={1: line}, anki=deck, _screenshots={1: b"png"},
                          prefs=SimpleNamespace(values={"ankiDeck": "Horu", "learnerLevel": "Beginner", "studyWith": "anki"}))
    app.card_for = lambda line_id, index: anki.Card(
        sentence=line["text"], word=line["words"][index]["surface"], dictionary_form=line["words"][index]["dictionary_form"],
        reading=line["words"][index]["dictionary_reading"], meaning="from JMdict", notes="",
    )
    return app


def test_card_tools_check_and_add():
    deck = FakeDeck(existing={"無用"})
    steps = []
    tools = {t.name: t for t in agent.card_tools(fake_app(deck), 1, steps.append)}
    assert json.loads(tools["check_deck"].call({"words": ["心配", "無用"]})[0]) == {"心配": False, "無用": True}
    result, error = tools["add_flashcard"].call({"word": "心配", "meaning": "worry (here: no need to worry)", "note": "心配無用 = no need to worry"})
    assert not error and result == "Added 心配 to the Horu deck."
    [(name, card, screenshot)] = deck.added
    assert card.meaning == "worry (here: no need to worry)" and card.notes == "心配無用 = no need to worry" and screenshot == b"png"
    assert tools["add_flashcard"].call({"word": "無用", "meaning": "useless", "note": ""})[0].startswith("Not added: 無用 is already")
    assert steps == ["Checking your Anki deck", "Adding 心配", "Adding 無用"]


def test_studying_in_horu_cards_become_saved_words(tmp_path):
    from horu.wordbook import Wordbook

    deck = FakeDeck()
    app = fake_app(deck)
    app.prefs.values["studyWith"] = "horu"
    app.words = Wordbook(tmp_path / "words.db")
    steps = []
    tools = {t.name: t for t in agent.card_tools(app, 1, steps.append)}
    assert json.loads(tools["check_deck"].call({"words": ["心配"]})[0]) == {"心配": False}
    result, error = tools["add_flashcard"].call({"word": "心配", "meaning": "worry", "note": "心配無用 = no need to worry"})
    assert not error and result == "Saved 心配 to the learner's words."
    [saved] = app.words.words()
    assert (saved["word"], saved["meaning"], saved["game"]) == ("心配", "worry", "P5R.exe") and app.words.image(saved["id"]) == b"png"
    assert json.loads(tools["check_deck"].call({"words": ["心配", "無用"]})[0]) == {"心配": True, "無用": False}
    assert tools["add_flashcard"].call({"word": "心配", "meaning": "worry", "note": ""})[0] == "Not added: 心配 is already in your words."
    assert deck.added == [] and steps == ["Checking your saved words", "Adding 心配", "Checking your saved words", "Adding 心配"]


def test_card_tools_respect_the_card_limit():
    deck = FakeDeck()
    app = fake_app(deck)
    app.lines[1]["words"] = [{"surface": w, "dictionary_form": w, "dictionary_reading": "", "grammar": []} for w in "一二三四"]
    app.card_for = lambda line_id, index: anki.Card(sentence="", word="一二三四"[index], dictionary_form="一二三四"[index], reading="", meaning="")
    add = {t.name: t for t in agent.card_tools(app, 1, lambda s: None)}["add_flashcard"]
    results = [add.call({"word": w, "meaning": "m", "note": ""})[0] for w in "一二三四"]
    assert len(deck.added) == agent.MAX_CARDS and "limit" in results[-1]


def test_follow_up_sends_the_model_back_once():
    client = FakeOpenAI(
        openai_response(content="I would call add_flashcard for 心配."),
        openai_response(calls=[("c1", "lookup_word", {"word": "心配"})]),
        openai_response(content="Done."),
    )
    provider = OpenAICompatProvider("qwen3:4b", "http://127.0.0.1:11434/v1", client=client)
    nudges = iter(["Call the tool instead of describing it.", None])
    assert provider.run_tools("S", "task", [echo_tool([])], follow_up=lambda answer: next(nudges)) == "Done."
    assert client.calls[1]["messages"][-1] == {"role": "user", "content": "Call the tool instead of describing it."}


def test_a_word_spanning_several_tokens_keeps_its_whole_form():
    deck = FakeDeck()
    app = fake_app(deck)
    app.lines[1]["text"] = "逃げ切る事だけ考えてくれ！"
    add = {t.name: t for t in agent.card_tools(app, 1, lambda s: None)}["add_flashcard"]
    assert add.call({"word": "逃げ切る", "meaning": "to get away", "note": ""})[0] == "Added 逃げ切る to the Horu deck."
    [(_, card, _)] = deck.added
    assert (card.word, card.dictionary_form, card.reading) == ("逃げ切る", "逃げ切る", "にげきる")


def test_card_note_reads_the_json_even_with_extra_text():
    from horu.tutor import Line, Tutor

    class Model:
        label = "fake"

        def __init__(self, reply):
            self.reply = reply

        def chat(self, system, effort="low", history=None, temperature=None):
            return SimpleNamespace(send=lambda text: iter([self.reply]))

    word = {"surface": "心配", "dictionary_form": "心配", "grammar": []}
    reply = 'Sure!\n{"meaning": "worry (here: no need to worry)", "note": "心配無用 = no need to worry"}'
    assert Tutor(Model(reply), "Beginner").card_note(Line("心配無用だ。"), word) == (
        "worry (here: no need to worry)",
        "心配無用 = no need to worry",
    )
    assert Tutor(Model("I can't do JSON"), "Beginner").card_note(Line("心配無用だ。"), word) == ("", "")


def test_make_cards_reports_the_cards_actually_added():
    deck = FakeDeck(existing={"無用"})
    app = fake_app(deck)

    class Model:
        def run_tools(self, system, task, tools, effort="low", max_turns=12, follow_up=None):
            t = {tool.name: tool for tool in tools}
            t["add_flashcard"].call({"word": "心配", "meaning": "worry", "note": ""})
            t["add_flashcard"].call({"word": "無用", "meaning": "unnecessary", "note": ""})  # refused: in deck
            return "Added 心配."

    app._provider = Model()
    app._line_context = lambda line_id: None
    result = agent.make_cards(app, 1, lambda step: None)
    assert result.added == ["心配"] and result.summary == "Added 心配."
