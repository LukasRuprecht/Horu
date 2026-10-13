from datetime import datetime, timedelta

from horu import jlpt
from horu.wordbook import Wordbook

# Noon, so a few hours either way stays on the same study day (days start at 4am).
NOON = datetime(2026, 10, 12, 12).timestamp()
DAY = 86400


def book(tmp_path):
    return Wordbook(tmp_path / "words.db")


def save(b, word, reading, now=NOON, **kw):
    return b.add(word=word, reading=reading, now=now, **{"meaning": "m", "game": "P5R.exe", **kw})


def test_saving_keeps_the_moment_and_refuses_duplicates(tmp_path):
    b = book(tmp_path)
    word, new = save(b, "予告状", "よこくじょう", sentence="予告状を出す", speaker="モルガナ", screenshot_png=b"png")
    assert new and word["status"] == "new" and word["sentence"] == "予告状を出す"
    assert b.image(word["id"]) == b"png"
    again, new = save(b, "予告状", "よこくじょう", sentence="another line")
    assert not new and again["id"] == word["id"] and again["sentence"] == "予告状を出す"
    b.remove(word["id"])
    assert b.words() == [] and b.image(word["id"]) is None


def test_new_words_then_reviews_come_back_when_due(tmp_path):
    b = book(tmp_path)
    first, _ = save(b, "本当", "ほんとう")
    save(b, "準備", "じゅんび", now=NOON + 1)
    card = b.next_card(new_per_day=10, now=NOON)
    assert card["id"] == first["id"]  # oldest new word first
    assert card["intervals"]["again"] < card["intervals"]["good"] < card["intervals"]["easy"]
    b.answer(first["id"], "easy", now=NOON)
    assert b.next_card(10, now=NOON)["word"] == "準備"
    b.answer(b.next_card(10, now=NOON)["id"], "easy", now=NOON)
    assert b.next_card(10, now=NOON) is None  # both scheduled days ahead
    later = NOON + 30 * DAY
    assert {w["word"] for w in b.words(now=later) if w["due_now"]} == {"本当", "準備"}
    assert b.next_card(10, now=later) is not None


def test_new_words_per_day_limit_and_learning_steps(tmp_path):
    b = book(tmp_path)
    for i, (w, r) in enumerate([("一", "いち"), ("二", "に"), ("三", "さん")]):
        save(b, w, r, now=NOON + i)
    first = b.next_card(new_per_day=2, now=NOON)
    b.answer(first["id"], "again", now=NOON)  # back in a minute
    second = b.next_card(2, now=NOON)
    assert second["id"] != first["id"]
    b.answer(second["id"], "good", now=NOON)
    # The day's two new words are started; the learning card shows up again, not the third.
    shown = b.next_card(2, now=NOON + 5)
    assert shown["id"] in (first["id"], second["id"])
    assert b.summary(2, now=NOON)["new"] == 0
    assert b.summary(2, now=NOON + DAY)["new"] == 1


def test_summary_counts_streak_week_and_games(tmp_path):
    b = book(tmp_path)
    a, _ = save(b, "本当", "ほんとう", now=NOON - 3 * DAY)
    z, _ = b.add(word="妖精", reading="ようせい", game="Zelda", now=NOON - 3 * DAY)
    for day in (2, 1, 0):  # reviewed on each of the last three days
        b.answer(a["id"], "good", now=NOON - day * DAY)
    s = b.summary(10, now=NOON)
    assert s["streak"] == 3 and s["reviewedToday"] == 1 and s["saved"] == 2
    assert [d["reviews"] for d in s["week"]] == [0, 0, 0, 0, 1, 1, 1]
    assert {g["game"]: g["words"] for g in s["games"]} == {"P5R.exe": 1, "Zelda": 1}
    # A missed day ends the streak.
    assert b.summary(10, now=NOON + 2 * DAY)["streak"] == 0


def test_filters(tmp_path):
    b = book(tmp_path)
    save(b, "本当", "ほんとう", meaning="truth")
    b.add(word="妖精", reading="ようせい", meaning="fairy", game="Zelda", now=NOON)
    assert [w["word"] for w in b.words(query="fairy")] == ["妖精"]
    assert [w["word"] for w in b.words(game="P5R.exe")] == ["本当"]
    assert len(b.words(status="new")) == 2 and b.words(status="known") == []
    assert set(b.games()) == {"P5R.exe", "Zelda"}


def test_jlpt_levels():
    assert jlpt.level("会う", "あう") == 5
    assert jlpt.level("準備", "じゅんび") == 4
    assert jlpt.level("ある", "アル") == 5  # katakana readings match too
    assert jlpt.level("予告状", "よこくじょう") is None


def test_day_starts_at_4am(tmp_path):
    b = book(tmp_path)
    a, _ = save(b, "本当", "ほんとう")
    late = datetime(2026, 10, 12, 23).timestamp()
    after_midnight = (datetime(2026, 10, 12) + timedelta(days=1, hours=2)).timestamp()
    b.answer(a["id"], "good", now=late)
    b.answer(a["id"], "good", now=after_midnight)
    assert b.summary(10, now=after_midnight)["reviewedToday"] == 2
