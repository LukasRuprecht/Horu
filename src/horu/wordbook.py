"""Words saved in Horu, each with the moment it came from, and when to review it.

Reviews are scheduled with FSRS (the algorithm Anki uses). Everything stays on this PC: a
SQLite file, and a folder of the screenshots the words were saved from.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fsrs import Card, Rating, Scheduler, State

DEFAULT_PATH = Path("data/words.db")

# A day starts at 4am, as in Anki: a late-night session counts for the day it began.
DAY_START = timedelta(hours=4)
# A word reviewed well for this long counts as known.
KNOWN_DAYS = 21
# When nothing is due, learning cards due this soon are shown anyway, rather than waiting.
LEARN_AHEAD = timedelta(minutes=20)
RATINGS = {"again": Rating.Again, "hard": Rating.Hard, "good": Rating.Good, "easy": Rating.Easy}

_SCHEMA = """
create table if not exists words (
    id integer primary key,
    word text not null,                  -- dictionary form
    reading text not null,
    surface text not null default '',    -- as written in the sentence
    meaning text not null default '',
    notes text not null default '',
    pos text not null default '',
    jlpt integer,                        -- 5 (N5) to 1 (N1), or null
    sentence text not null default '',
    speaker text not null default '',
    translation text not null default '',
    game text not null default '',
    image text,                          -- file in the images folder: the line's screenshot
    added real not null,
    card text not null,                  -- the FSRS card, as JSON
    due real not null,
    reps integer not null default 0,
    unique (word, reading)
);
create table if not exists reviews (
    id integer primary key,
    word_id integer not null,
    time real not null,
    rating integer not null
);
create index if not exists reviews_time on reviews (time);
"""

_COLUMNS = "id, word, reading, surface, meaning, notes, pos, jlpt, sentence, speaker, translation, game, image, added, card, due, reps"


class Wordbook:
    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        self.path = Path(path)
        self.images = self.path.parent / "word_images"
        self.scheduler = Scheduler(enable_fuzzing=False)
        self._lock = threading.Lock()
        self._db: sqlite3.Connection | None = None

    @property
    def db(self) -> sqlite3.Connection:
        if self._db is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(self.path, check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            self._db.executescript(_SCHEMA)
        return self._db

    # --- saving ---------------------------------------------------------------------------

    def add(self, *, word: str, reading: str, surface: str = "", meaning: str = "", notes: str = "",
            pos: str = "", jlpt: int | None = None, sentence: str = "", speaker: str = "",
            translation: str = "", game: str = "", screenshot_png: bytes | None = None,
            now: float | None = None) -> tuple[dict, bool]:
        """Save a word; returns (the word, whether it was new). A word already saved keeps its
        first moment and its schedule."""
        now = time.time() if now is None else now
        with self._lock:
            found = self.db.execute(f"select {_COLUMNS} from words where word = ? and reading = ?", (word, reading)).fetchone()
            if found:
                return self._public(found, now), False
            card = Card(due=_dt(now))
            cursor = self.db.execute(
                "insert into words (word, reading, surface, meaning, notes, pos, jlpt, sentence, speaker, translation, game, added, card, due) "
                "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (word, reading, surface or word, meaning, notes, pos, jlpt, sentence, speaker, translation, game, now,
                 json.dumps(card.to_dict()), now),
            )
            word_id = cursor.lastrowid
            if screenshot_png:
                self.images.mkdir(parents=True, exist_ok=True)
                (self.images / f"{word_id}.png").write_bytes(screenshot_png)
                self.db.execute("update words set image = ? where id = ?", (f"{word_id}.png", word_id))
            self.db.commit()
            return self.get(word_id, now), True

    def get(self, word_id: int, now: float | None = None) -> dict | None:
        row = self.db.execute(f"select {_COLUMNS} from words where id = ?", (word_id,)).fetchone()
        return row and self._public(row, time.time() if now is None else now)

    def remove(self, word_id: int) -> None:
        with self._lock:
            self.db.execute("delete from words where id = ?", (word_id,))
            self.db.execute("delete from reviews where word_id = ?", (word_id,))
            self.db.commit()
        (self.images / f"{word_id}.png").unlink(missing_ok=True)

    def image(self, word_id: int) -> bytes | None:
        path = self.images / f"{word_id}.png"
        return path.read_bytes() if path.exists() else None

    def words(self, *, query: str = "", game: str = "", status: str = "", now: float | None = None) -> list[dict]:
        """Saved words, newest first, filtered by text (Japanese or English), game and status
        ("new", "learning", "known")."""
        now = time.time() if now is None else now
        sql, params = f"select {_COLUMNS} from words where 1", []
        if query:
            sql += " and (word like ? or reading like ? or meaning like ? or sentence like ?)"
            params += [f"%{query}%"] * 4
        if game:
            sql += " and game = ?"
            params.append(game)
        found = [self._public(row, now) for row in self.db.execute(sql + " order by added desc", params)]
        return [w for w in found if not status or w["status"] == status]

    def games(self) -> list[str]:
        return [row[0] for row in self.db.execute("select game from words group by game order by max(added) desc")]

    # --- reviewing ------------------------------------------------------------------------

    def next_card(self, new_per_day: int, now: float | None = None) -> dict | None:
        """The next word to review: one that's due, then a new one while today's new words
        last, then a learning step due soon. None when there's nothing left today."""
        now = time.time() if now is None else now
        row = self.db.execute(f"select {_COLUMNS} from words where reps > 0 and due <= ? order by due limit 1", (now,)).fetchone()
        if row is None and self._new_left(new_per_day, now) > 0:
            row = self.db.execute(f"select {_COLUMNS} from words where reps = 0 order by added limit 1").fetchone()
        if row is None:
            soon = now + LEARN_AHEAD.total_seconds()
            row = self.db.execute(
                f"select {_COLUMNS} from words where reps > 0 and due <= ? and json_extract(card, '$.state') != ? order by due limit 1",
                (soon, State.Review.value),
            ).fetchone()
        if row is None:
            return None
        word = self._public(row, now)
        card = Card.from_dict(json.loads(row["card"]))
        word["intervals"] = {
            name: (self.scheduler.review_card(Card.from_dict(card.to_dict()), rating, _dt(now))[0].due - _dt(now)).total_seconds()
            for name, rating in RATINGS.items()
        }
        return word

    def answer(self, word_id: int, rating: str, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        with self._lock:
            row = self.db.execute("select card, reps from words where id = ?", (word_id,)).fetchone()
            if row is None:
                raise KeyError(word_id)
            card, _ = self.scheduler.review_card(Card.from_dict(json.loads(row["card"])), RATINGS[rating], _dt(now))
            self.db.execute(
                "update words set card = ?, due = ?, reps = reps + 1 where id = ?",
                (json.dumps(card.to_dict()), card.due.timestamp(), word_id),
            )
            self.db.execute("insert into reviews (word_id, time, rating) values (?, ?, ?)", (word_id, now, RATINGS[rating].value))
            self.db.commit()
        return self.get(word_id, now)

    def summary(self, new_per_day: int, now: float | None = None) -> dict:
        """Today's numbers, the last 7 days of reviews, the streak, and progress per game."""
        now = time.time() if now is None else now
        words = self.words(now=now)
        today = _day(now)
        reviewed_days = {_day(t) for (t,) in self.db.execute("select time from reviews")}
        streak, day = 0, today if today in reviewed_days else today - 1
        while day in reviewed_days:
            streak, day = streak + 1, day - 1
        week_start = _day_start(today - 6)
        per_day = [0] * 7
        for (t,) in self.db.execute("select time from reviews where time >= ?", (week_start,)):
            per_day[min(6, _day(t) - (today - 6))] += 1
        games: dict[str, dict] = {}
        for w in words:
            g = games.setdefault(w["game"], {"game": w["game"], "words": 0, "known": 0, "due": 0})
            g["words"] += 1
            g["known"] += w["status"] == "known"
            g["due"] += w["due_now"]
        return {
            "due": sum(w["due_now"] for w in words),
            "new": min(self._new_left(new_per_day, now), sum(w["status"] == "new" for w in words)),
            "saved": len(words),
            "known": sum(w["status"] == "known" for w in words),
            "reviewedToday": per_day[6],
            "streak": streak,
            "week": [{"day": datetime.fromtimestamp(_day_start(today - 6 + i)).strftime("%a"), "reviews": n} for i, n in enumerate(per_day)],
            "games": sorted(games.values(), key=lambda g: -g["words"]),
        }

    def _new_left(self, new_per_day: int, now: float) -> int:
        """New words still to start today: each word's first review counts once."""
        started = self.db.execute(
            "select count(distinct word_id) from reviews r where time >= ? and not exists "
            "(select 1 from reviews earlier where earlier.word_id = r.word_id and earlier.time < ?)",
            (_day_start(_day(now)), _day_start(_day(now))),
        ).fetchone()[0]
        return max(0, new_per_day - started)

    def _public(self, row: sqlite3.Row, now: float) -> dict:
        card = Card.from_dict(json.loads(row["card"]))
        if row["reps"] == 0:
            status = "new"
        elif card.state == State.Review and (card.stability or 0) >= KNOWN_DAYS:
            status = "known"
        else:
            status = "learning"
        word = {key: row[key] for key in row.keys() if key not in ("card",)}
        word["status"] = status
        word["due_now"] = row["reps"] > 0 and row["due"] <= now
        return word


def _dt(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, timezone.utc)


def _day(timestamp: float) -> int:
    """The local day a moment belongs to (days start at 4am), as a day number."""
    local = datetime.fromtimestamp(timestamp) - DAY_START
    return local.toordinal()


def _day_start(day: int) -> float:
    return (datetime.fromordinal(day) + DAY_START).timestamp()
