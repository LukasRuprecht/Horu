from types import SimpleNamespace

from horu import correction
from horu.app import App


def test_a_fix_is_applied_only_when_two_sources_agree():
    text, alternatives = "近くへ来て足につまりなさい。", ["近くへ来て足につかまりなさい。"]
    # The vision model read what another OCR model read: apply.
    assert correction.decide(text, "近くへ来て足につかまりなさい。", alternatives) == correction.APPLY
    # Only one source has this reading (the tutor's wrong guess): suggest, don't replace.
    assert correction.decide(text, "近くへ来て足にすがりなさい。", alternatives) == correction.SUGGEST
    # Spacing and width aren't a fix.
    assert correction.decide("さあ勇気を出して！", "さあ 勇気を出して!", []) == correction.SAME


def test_ocr_line_in_a_reply():
    reply = "OCR: 「近くへ来て足につかまりなさい。」\n**つかまる** means to hold on."
    assert correction.proposed_in(reply) == "近くへ来て足につかまりなさい。"
    assert correction.without_proposal(reply) == "**つかまる** means to hold on."
    assert correction.proposed_in("Come closer and grab my leg.") is None
    assert correction.without_proposal("Come closer.") == "Come closer."


def fake_app(text, alternatives=(), ai_reading=None):
    calls = []
    line = {"text": text, "alternatives": list(alternatives), "aiReading": ai_reading, "suggestion": None}
    app = SimpleNamespace(lines={1: line}, _call=lambda name, payload: calls.append((name, payload)))
    app.update_line = lambda line_id, new: line.update(text=new) or ["words"]
    return app, line, calls


def test_propose_applies_agreeing_fixes_and_suggests_the_rest():
    app, line, calls = fake_app("マツプ", ["マップ"])
    assert App.propose(app, 1, "マップ", "vision", retranslate=False) == correction.APPLY
    assert line["text"] == "マップ" and line["was"] == "マツプ"
    assert calls == [("lineCorrected", {"id": 1, "text": "マップ", "words": ["words"], "was": "マツプ", "retranslate": False})]

    app, line, calls = fake_app("足につまりなさい。")
    assert App.propose(app, 1, "足にすがりなさい。", "explain") == correction.SUGGEST
    assert line["text"] == "足につまりなさい。" and line["suggestion"] == "足にすがりなさい。"
    assert calls[0][0] == "lineSuggestion"


def test_the_tutor_agreeing_with_the_vision_model_is_enough():
    app, line, calls = fake_app("太陽はに月は太陽に…", ai_reading="太陽は月に…月は太陽に…")
    assert App.propose(app, 1, "太陽は月に…月は太陽に…", "explain") == correction.APPLY


def test_rewrites_are_not_fixes():
    # A small model "correcting" dialect it doesn't know.
    assert correction.decide("てやんでぇべらぼうめい！", "ベルがいいね！", []) == correction.IGNORE
    # A garbled vision reading.
    assert correction.decide("そうび", "そうびげろびび", []) == correction.IGNORE


def test_near_agreement_on_the_same_fix_counts():
    # Gemini and another OCR model both restore 月; they differ by one … elsewhere.
    text, other = "太陽はに月は太陽に…", "太陽は月に月は太陽に…"
    assert correction.decide(text, "太陽は月に…月は太陽に…", [other]) == correction.APPLY
    # A guess no closer to the other reading than the original is stays a suggestion.
    assert correction.decide("足につまりなさい。", "足にすがりなさい。", ["足につかまりなさい。"]) == correction.SUGGEST


def test_readings_are_dropped_from_a_proposal():
    assert correction.proposed_in("OCR: 太陽（たいよう）は月に…月(つき)は太陽に…\nThe sun...") == "太陽は月に…月は太陽に…"
