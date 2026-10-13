"""Which AI requests new lines cause: as few as possible, without skipping a check that matters."""

import dataclasses
import time
from pathlib import Path

import pytest

from horu import ai_setup, app as horu_app, config
from horu.translations import TranslationMemory
from horu.tutor import numbered_lines

ROOT = Path(__file__).resolve().parent.parent


class FakeChat:
    def __init__(self, provider):
        self._provider = provider

    def send(self, text, image=None):
        self._provider.sent.append({"text": text, "image": image is not None})
        yield self._provider.reply(text, image)

    def cancel(self):
        pass


class FakeProvider:
    label = "fake"

    def __init__(self, reply=None):
        self.sent = []
        self.reply = reply or (lambda text, image: "A translation.")

    def chat(self, system, effort="low", history=None, temperature=None):
        return FakeChat(self)

    def read_image(self, prompt, png, effort="low"):
        self.sent.append({"text": prompt, "image": True, "check": True})
        return "佐倉惣治郎"


@pytest.fixture
def app(tmp_path, monkeypatch):
    cfg = dataclasses.replace(config.load(ROOT / "config.example.toml"), save_captures=False)
    monkeypatch.chdir(tmp_path)  # every data/ file the app opens lands here
    a = horu_app.App(cfg)
    a.prefs.values.update(autoTranslate=True, aiChecks=True, applyAiReadings=True, autoCards=False)
    a._call = lambda function, *args: a.calls.append((function, args))
    a.calls = []
    a._ai = ai_setup.Resolved(provider=FakeProvider())
    return a


def add(app, text, uncertain=False, speaker=None, kind="dialogue", game="P5R.exe"):
    line_id = next(app._ids)
    app.lines[line_id] = {
        "id": line_id, "text": text, "kind": kind, "uncertain": uncertain, "repeat": False, "game": game,
        "alternatives": [], "words": [],
        "speaker": {"text": speaker, "uncertain": False, "alternatives": [], "english": "Sojiro"} if speaker else None,
    }
    app._screenshots[line_id] = b"png"
    return app.lines[line_id]


def settle(app):
    deadline = time.monotonic() + 5
    while app._ai_busy() and time.monotonic() < deadline:
        time.sleep(0.01)


def test_a_sure_line_is_translated_without_a_check(app):
    app.speakers.set_english("P5R.exe", "佐倉惣治郎", "Sojiro Sakura", "ai")
    line = add(app, "いいから食え。", speaker="佐倉惣治郎")
    app._ai_for([line])
    settle(app)
    assert [s["image"] for s in app._ai.provider.sent] == [False]
    assert line["translation"] == "A translation."


def test_an_unsure_line_is_checked_in_its_translation_request(app):
    app._ai.provider.reply = lambda text, image: "OCR: いいから食え。\nJust eat."
    line = add(app, "いいから合え。", uncertain=True)
    app._ai_for([line])
    settle(app)
    [request] = app._ai.provider.sent
    assert request["image"] and "OCR read it as" in request["text"]
    # The fix was applied with its translation: the page isn't asked to translate again.
    assert line["text"] == "いいから食え。" and line["translation"] == "Just eat." and not line["uncertain"]
    corrected = [args[0] for name, args in app.calls if name == "lineCorrected"]
    assert corrected and corrected[0]["translated"] is True


def test_no_fix_means_the_ai_agreed(app):
    line = add(app, "いいから食え。", uncertain=True)
    app._ai_for([line])
    settle(app)
    assert not line["uncertain"] and ("lineVerified", ({"id": line["id"]},)) in app.calls


def test_lines_shown_together_share_one_request(app):
    app._ai.provider.reply = lambda text, image: "1. Curry...\n2. In the morning?"
    lines = [add(app, "カレーか…"), add(app, "朝から？")]
    app._ai_for(lines)
    settle(app)
    [request] = app._ai.provider.sent
    assert "1. 「カレーか…」" in request["text"] and "2. 「朝から？」" in request["text"]
    assert [l["translation"] for l in lines] == ["Curry...", "In the morning?"]


def test_a_line_missing_from_a_shared_reply_is_translated_on_its_own(app):
    app._ai.provider.reply = lambda text, image: "1. Curry..." if "1." in text else "In the morning?"
    lines = [add(app, "カレーか…"), add(app, "朝から？")]
    app._ai_for(lines)
    settle(app)
    assert len(app._ai.provider.sent) == 2 and lines[1]["translation"] == "In the morning?"


def test_a_line_seen_before_reuses_its_translation(app):
    first = add(app, "いらっしゃい。")
    app._ai_for([first])
    settle(app)
    again = add(app, "いらっしゃい。")
    assert app._remembered(again) and again["translation"] == "A translation."
    # An unsure read of text only translated, never checked, isn't trusted...
    app.translations.put("P5R.exe", None, "いらっしゃる。", "Welcome.", verified=False)
    assert not app._remembered(add(app, "いらっしゃる。", uncertain=True))
    # ...but once the AI read that text from a screenshot, it is.
    app.translations.put("P5R.exe", None, "いらっしゃる。", "Welcome.", verified=True)
    unsure = add(app, "いらっしゃる。", uncertain=True)
    assert app._remembered(unsure) and not unsure["uncertain"]


def test_other_text_is_checked_only_when_unsure(app):
    sure, unsure = add(app, "アイテム", kind="other"), add(app, "セープ", kind="other", uncertain=True)
    app._ai_for([sure, unsure])
    settle(app)
    assert [s.get("check") for s in app._ai.provider.sent] == [True]


def test_a_text_only_model_translates_without_the_screenshot(app):
    from horu.llm import AIError

    def reply(text, image):
        if image is not None:
            raise AIError("m can't read images. Choose a vision model.")
        return "Just eat."

    app._ai.provider.reply = reply
    line = add(app, "いいから合え。", uncertain=True)
    app._ai_for([line])
    settle(app)
    assert line["translation"] == "Just eat." and app._vision is False


def test_translation_memory(tmp_path):
    memory = TranslationMemory(tmp_path / "t.db")
    assert memory.get("g", None, "あ") is None
    memory.put("g", "佐倉惣治郎", "あ", "Ah.", verified=True)
    memory.put("g", "佐倉惣治郎", "あ", "Oh.", verified=False)  # translated again
    assert memory.get("g", "佐倉惣治郎", "あ") == ("Oh.", True)  # verified stays
    assert memory.get("g", None, "あ") is None


def test_numbered_replies_are_parsed():
    reply = "1. Curry...\n2) In the morning?\n3：「Fine.」\nNote: none\n2. again"
    assert numbered_lines(reply) == {1: "Curry...", 2: "In the morning?", 3: "Fine."}
