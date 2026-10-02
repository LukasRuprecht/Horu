from types import SimpleNamespace

import pytest

from horu.llm.anthropic_provider import AnthropicProvider
from horu.llm.base import AIError
from horu.llm.openai_compat import OpenAICompatProvider


class FakeAnthropicStream:
    def __init__(self, chunks, stop_reason="end_turn"):
        self.text_stream = iter(chunks)
        self._final = SimpleNamespace(
            stop_reason=stop_reason, content=[{"type": "text", "text": "".join(chunks)}]
        )

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._final


class FakeAnthropicClient:
    """Records each stream() call; replies with the queued chunk lists in order."""

    def __init__(self, *replies, stop_reason="end_turn"):
        self.calls = []
        self._replies = list(replies)
        self._stop_reason = stop_reason
        messages = SimpleNamespace(stream=self._stream)
        self.messages = messages
        self.beta = SimpleNamespace(messages=messages)

    def _stream(self, **kwargs):
        # Snapshot: the chat passes its live history list.
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return FakeAnthropicStream(self._replies.pop(0), self._stop_reason)


def test_claude_request_shape_and_append_only_history():
    client = FakeAnthropicClient(["Hel", "lo"], ["Again"])
    chat = AnthropicProvider("claude-opus-5-5", client=client).chat("SYSTEM", effort="low")

    assert "".join(chat.send("first")) == "Hello"
    assert "".join(chat.send("second")) == "Again"

    first, second = client.calls
    assert first["model"] == "claude-opus-5-5"
    assert first["output_config"] == {"effort": "low"}
    assert first["fallbacks"] == "default" and first["betas"] == ["server-side-fallback-2026-07-01"]
    assert "thinking" not in first  # Opus 5.5 can't disable thinking; effort is the control
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    # The second request replays the first exchange unchanged, with the full response content.
    assert second["messages"] == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": [{"type": "text", "text": "Hello"}]},
        {"role": "user", "content": "second"},
    ]


def test_claude_models_without_fallbacks_or_effort():
    client = FakeAnthropicClient(["ok"])
    list(AnthropicProvider("claude-haiku-4-5", client=client).chat("S").send("hi"))
    [call] = client.calls
    assert "fallbacks" not in call and "betas" not in call and "output_config" not in call


def test_claude_refusal_is_reported_and_not_added_to_history():
    client = FakeAnthropicClient(["partial"], ["fine"], stop_reason="refusal")
    chat = AnthropicProvider("claude-opus-5-5", client=client).chat("S")
    with pytest.raises(AIError, match="declined"):
        list(chat.send("bad"))
    client._stop_reason = "end_turn"
    list(chat.send("good"))
    assert client.calls[1]["messages"] == [{"role": "user", "content": "good"}]


def test_cancel_stops_streaming():
    client = FakeAnthropicClient(["a", "b", "c"])
    chat = AnthropicProvider("claude-opus-5-5", client=client).chat("S")
    received = []
    for chunk in chat.send("hi"):
        received.append(chunk)
        chat.cancel()
    assert received == ["a"]


class FakeOpenAIClient:
    def __init__(self, *replies):
        self.calls = []
        self._replies = list(replies)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        chunks = [
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=c))])
            for c in self._replies.pop(0)
        ]
        # Some servers send a final usage-only chunk without choices.
        chunks.append(SimpleNamespace(choices=[]))
        return FakeOpenAIStream(chunks)


class FakeOpenAIStream(list):
    def close(self):
        pass


def test_openai_compat_streams_and_keeps_history():
    client = FakeOpenAIClient(["こん", "にちは"], ["ok"])
    provider = OpenAICompatProvider("qwen3:8b", "http://localhost:11434/v1", client=client)
    chat = provider.chat("SYSTEM")
    assert "".join(chat.send("first")) == "こんにちは"
    list(chat.send("second"))
    assert client.calls[1]["messages"] == [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "こんにちは"},
        {"role": "user", "content": "second"},
    ]
    assert client.calls[0]["stream"] is True
    assert provider.label == "qwen3:8b (localhost)"
