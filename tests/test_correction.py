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


def fake_app(text, alternatives=(), ai_reading=None, apply_ai_readings=False):
    calls = []
    line = {"text": text, "alternatives": list(alternatives), "aiReading": ai_reading, "suggestion": None}
    app = SimpleNamespace(lines={1: line}, _call=lambda name, payload: calls.append((name, payload)))
    app.prefs = SimpleNamespace(values={"applyAiReadings": apply_ai_readings})
    app.update_line = lambda line_id, new: line.update(text=new) or ["words"]
    return app, line, calls


def test_propose_applies_agreeing_fixes_and_suggests_the_rest():
    app, line, calls = fake_app("マツプ", ["マップ"])
    assert App.propose(app, 1, "マップ", "vision") == correction.APPLY
    assert line["text"] == "マップ" and line["was"] == "マツプ"
    assert calls == [("lineCorrected", {"id": 1, "text": "マップ", "words": ["words"], "was": "マツプ", "translated": False})]

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


def test_a_vision_reading_that_matches_clears_the_flag():
    app, line, calls = fake_app("よくきたなLuke。")
    line["uncertain"] = True
    assert App.propose(app, 1, "よくきたな Luke。", "vision") == correction.SAME
    assert line["uncertain"] is False and calls == [("lineVerified", {"id": 1})]


def test_fixes_that_cannot_change_the_meaning_are_applied():
    # A stray ー read from the gap before セーブ.
    text = "もし休憩するならスタートボタンでーセーブしとくのわすれずにネ。"
    assert correction.decide(text, "もし休憩するならスタートボタンでセーブしとくのわすれずにネ。", []) == correction.APPLY
    assert correction.decide("うつかり近づかんようにな。", "うっかり近づかんようにな。", []) == correction.APPLY
    assert correction.decide("行くよ", "行くよ。", []) == correction.APPLY


def test_fixes_that_change_words_are_still_suggestions():
    assert correction.decide("セーブしておこう", "セブしておこう", []) == correction.SUGGEST  # a katakana long vowel
    assert correction.decide("なかなかいい詩だ", "なかなかいい歌だ", []) == correction.SUGGEST
    assert correction.decide("てやんでぇ", "てやんでえ", []) == correction.SUGGEST  # small vowels are a spelling choice


def test_with_the_preference_on_the_ai_reading_is_used_at_once():
    app, line, calls = fake_app("足につまりなさい。", apply_ai_readings=True)
    assert App.propose(app, 1, "足にすがりなさい。", "vision") == correction.APPLY
    assert line["text"] == "足にすがりなさい。" and line["was"] == "足につまりなさい。"
    assert calls[0][0] == "lineCorrected"
    # A rewrite is still ignored.
    app, line, calls = fake_app("てやんでぇべらぼうめい！", apply_ai_readings=True)
    assert App.propose(app, 1, "ベルがいいね！", "vision") == correction.IGNORE and not calls


def test_an_applied_reading_keeps_the_games_spaces():
    app, line, calls = fake_app("あ\u3000いそがし…", apply_ai_readings=True)
    App.propose(app, 1, "あ いそがしい", "vision")
    assert line["text"] == "あ　いそがしい"
    # A reading of a different length can't be lined up: it's used as it is.
    assert correction.keep_spaces("あ　いそがし…", "あいそがしい…") == "あいそがしい…"
    assert correction.keep_spaces("あ\u3000いそがし…", "あいそかし…") == "あ\u3000いそかし…"
