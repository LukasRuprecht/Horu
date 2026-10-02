"""Offline English dictionary lookups using JMdict.

JMdict (https://www.edrdg.org/jmdict/j_jmdict.html) is the property of the Electronic
Dictionary Research and Development Group and is used under the CC BY-SA 4.0 licence. It
is not bundled with Horu: `horu setup` downloads it and builds a local SQLite index.
"""

from __future__ import annotations

import gzip
import io
import json
import re
import sqlite3
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

JMDICT_URL = "http://ftp.edrdg.org/pub/Nihongo/JMdict_e.gz"
DEFAULT_PATH = Path("data/jmdict.sqlite")
_XML_ENTITIES = {"amp", "lt", "gt", "quot", "apos"}
_PRIORITY_TAGS = {"news1", "ichi1", "spec1", "spec2", "gai1"}

# Short labels for the most common JMdict part-of-speech codes; others use JMdict's own text.
POS_LABELS = {
    "n": "noun", "pn": "pronoun", "adv": "adverb", "prt": "particle", "conj": "conjunction",
    "int": "interjection", "exp": "expression", "adj-i": "い-adjective", "adj-na": "な-adjective",
    "adj-no": "の-adjective", "adj-pn": "pre-noun", "aux-v": "auxiliary verb", "cop": "copula",
    "v1": "ichidan verb", "vs": "する verb", "vs-i": "する verb", "vk": "kuru verb", "vt": "transitive",
    "vi": "intransitive", "suf": "suffix", "pref": "prefix", "ctr": "counter", "num": "numeric",
}

# Horu's word types -> JMdict POS code prefixes that count as a match.
_POS_MATCH = {
    "noun": ("n", "vs"), "name": ("n",), "pronoun": ("pn",), "verb": ("v",), "adjective": ("adj-i",),
    "na-adjective": ("adj-na",), "adverb": ("adv",), "particle": ("prt",), "auxiliary": ("aux", "cop"),
    "conjunction": ("conj",), "interjection": ("int",), "pre-noun": ("adj-pn",), "prefix": ("pref",),
    "suffix": ("suf",),
}


class DictionaryMissing(Exception):
    pass


def build(path: Path = DEFAULT_PATH, source: Path | None = None) -> int:
    """Download (unless `source` is given) and index JMdict. Returns the number of entries."""
    if source is None:
        print(f"Downloading {JMDICT_URL} ...")
        with urllib.request.urlopen(JMDICT_URL) as response:
            raw = response.read()
    else:
        raw = source.read_bytes()
    text = gzip.decompress(raw).decode("utf-8")

    # JMdict codes are XML entities declared in the DTD (<!ENTITY v5k "Godan verb...">).
    # Keep the short codes and store the descriptions separately.
    descriptions = dict(re.findall(r'<!ENTITY ([\w-]+) "([^"]*)">', text))
    body = text[text.index("<JMdict>") :]
    body = re.sub(r"&([\w-]+);", lambda m: m.group(0) if m.group(1) in _XML_ENTITIES else m.group(1), body)

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    db.executescript(
        """
        CREATE TABLE entries (id INTEGER PRIMARY KEY, common INTEGER NOT NULL, data TEXT NOT NULL);
        CREATE TABLE forms (form TEXT NOT NULL, entry_id INTEGER NOT NULL);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """
    )
    count = 0
    for _, elem in ET.iterparse(io.BytesIO(body.encode("utf-8"))):
        if elem.tag != "entry":
            continue
        entry_id, entry, common = _parse_entry(elem)
        db.execute("INSERT INTO entries VALUES (?, ?, ?)", (entry_id, common, json.dumps(entry, ensure_ascii=False)))
        db.executemany(
            "INSERT INTO forms VALUES (?, ?)", [(f, entry_id) for f in {*entry["kanji"], *entry["readings"]}]
        )
        elem.clear()
        count += 1
    db.execute("CREATE INDEX forms_form ON forms(form)")
    db.execute("INSERT INTO meta VALUES ('pos_descriptions', ?)", (json.dumps(descriptions, ensure_ascii=False),))
    db.commit()
    db.close()
    tmp.replace(path)
    return count


def _parse_entry(elem: ET.Element) -> tuple[int, dict, int]:
    kanji = [k.findtext("keb") for k in elem.iter("k_ele")]
    readings = [r.findtext("reb") for r in elem.iter("r_ele")]
    common = any(p.text in _PRIORITY_TAGS for p in [*elem.iter("ke_pri"), *elem.iter("re_pri")])
    senses, pos = [], []
    for sense in elem.iter("sense"):
        # A sense without <pos> inherits the previous sense's parts of speech.
        pos = [p.text for p in sense.iter("pos")] or pos
        senses.append(
            {
                "pos": pos,
                "gloss": [g.text for g in sense.iter("gloss")],
                "misc": [m.text for m in sense.iter("misc")],
                "info": [i.text for i in sense.iter("s_inf")],
            }
        )
    return int(elem.findtext("ent_seq")), {"kanji": kanji, "readings": readings, "senses": senses}, int(common)


class Dictionary:
    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        if not path.exists():
            raise DictionaryMissing(f"Dictionary not found at {path}. Run `uv run horu setup`.")
        # The UI looks words up from a different thread than the one that opened the DB.
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._pos_descriptions = json.loads(
            self._db.execute("SELECT value FROM meta WHERE key = 'pos_descriptions'").fetchone()[0]
        )

    def lookup(self, word: dict, limit: int = 3) -> list[dict]:
        """Best JMdict entries for a word from `horu.nlp.Word.to_dict()`."""
        scored: dict[int, tuple[float, dict]] = {}
        for rank, form in enumerate(word["lookup_forms"]):
            rows = self._db.execute(
                "SELECT e.id, e.common, e.data FROM forms f JOIN entries e ON e.id = f.entry_id WHERE f.form = ?",
                (form,),
            ).fetchall()
            for entry_id, common, data in rows:
                if entry_id in scored:
                    continue
                entry = json.loads(data)
                scored[entry_id] = (self._score(entry, word, common, rank), entry)
        best = sorted(scored.values(), key=lambda s: s[0], reverse=True)[:limit]
        return [self._present(entry) for _, entry in best]

    def _score(self, entry: dict, word: dict, common: int, form_rank: int) -> float:
        score = 2.0 * common - form_rank
        if word["lemma"] in entry["kanji"] or word["dictionary_form"] in entry["kanji"]:
            score += 4  # UniDic's lemma pins the kanji even when the text is in kana (なる -> 成る)
        if word["dictionary_reading"] in entry["readings"]:
            score += 2
        prefixes = _POS_MATCH.get(word["pos"], ())
        if any(p.startswith(prefixes) for s in entry["senses"] for p in s["pos"]):
            score += 3
        return score

    def _pos_label(self, code: str) -> str:
        if code.startswith("v5"):
            return "godan verb"
        return POS_LABELS.get(code) or self._pos_descriptions.get(code, code)

    def _present(self, entry: dict) -> dict:
        senses = []
        for s in entry["senses"]:
            senses.append(
                {
                    "pos": [self._pos_label(p) for p in s["pos"]],
                    "gloss": s["gloss"],
                    "notes": [self._pos_descriptions.get(m, m) for m in s["misc"]] + s["info"],
                }
            )
        return {"kanji": entry["kanji"], "readings": entry["readings"], "senses": senses}
