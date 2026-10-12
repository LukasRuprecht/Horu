"""Offline English dictionary lookups using JMdict, and name readings using JMnedict.

JMdict (https://www.edrdg.org/jmdict/j_jmdict.html) and JMnedict
(https://www.edrdg.org/enamdict/enamdict_doc.html) are the property of the Electronic
Dictionary Research and Development Group and are used under the CC BY-SA 4.0 licence.
They are not bundled with Horu: `horu setup` downloads them and builds a local SQLite index.
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
from typing import Callable

JMDICT_URL = "http://ftp.edrdg.org/pub/Nihongo/JMdict_e.gz"
JMNEDICT_URL = "http://ftp.edrdg.org/pub/Nihongo/JMnedict.xml.gz"
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

# JMnedict name types kept (people, characters, places, myth); companies, stations, products
# and unclassified entries are left out to keep the index small.
NAME_TYPES = {
    "surname": "surname",
    "given": "given name",
    "fem": "female given name",
    "masc": "male given name",
    "person": "full name",
    "char": "character",
    "fict": "fictional",
    "creat": "creature",
    "myth": "mythology",
    "dei": "deity",
    "leg": "legend",
    "place": "place",
}
_PERSON_TYPES = {"surname", "given", "fem", "masc", "person", "char", "fict"}

# Horu's word types -> JMdict POS code prefixes that count as a match.
_POS_MATCH = {
    "noun": ("n", "vs"), "name": ("n",), "pronoun": ("pn",), "verb": ("v",), "adjective": ("adj-i",),
    "na-adjective": ("adj-na",), "adverb": ("adv",), "particle": ("prt",), "auxiliary": ("aux", "cop"),
    "conjunction": ("conj",), "interjection": ("int",), "pre-noun": ("adj-pn",), "prefix": ("pref",),
    "suffix": ("suf",),
}


class DictionaryMissing(Exception):
    pass


# Called with (bytes downloaded, total bytes or 0 when unknown) while downloading.
Progress = Callable[[int, int], None]


def _download(url: str, progress: Progress | None) -> bytes:
    print(f"Downloading {url} ...")
    with urllib.request.urlopen(url, timeout=60) as response:
        total = int(response.headers.get("Content-Length") or 0)
        chunks, done = [], 0
        while chunk := response.read(1 << 16):
            chunks.append(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    return b"".join(chunks)


def _load_xml(url: str, source: Path | None, root: str, progress: Progress | None = None) -> tuple[dict[str, str], bytes]:
    """Download (unless `source` is given) an EDRDG dictionary and prepare it for parsing."""
    if source is None:
        raw = _download(url, progress)
    else:
        raw = source.read_bytes()
    text = gzip.decompress(raw).decode("utf-8")
    # Codes are XML entities declared in the DTD (<!ENTITY v5k "Godan verb...">). Keep the
    # short codes and return the descriptions separately.
    descriptions = dict(re.findall(r'<!ENTITY ([\w-]+) "([^"]*)">', text))
    body = text[text.index(f"<{root}>") :]
    body = re.sub(r"&([\w-]+);", lambda m: m.group(0) if m.group(1) in _XML_ENTITIES else m.group(1), body)
    return descriptions, body.encode("utf-8")


def build(
    path: Path = DEFAULT_PATH,
    source: Path | None = None,
    names_source: Path | None = None,
    progress: Callable[[str, int, int], None] | None = None,
) -> int:
    """Download (unless sources are given) and index JMdict and JMnedict. Returns the number
    of JMdict entries. `progress` is called with (which dictionary, bytes, total bytes)."""
    descriptions, body = _load_xml(JMDICT_URL, source, "JMdict", progress and (lambda d, t: progress("JMdict", d, t)))

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
    for _, elem in ET.iterparse(io.BytesIO(body)):
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
    _build_names(db, names_source, progress)
    db.commit()
    db.close()
    tmp.replace(path)
    return count


def build_names(path: Path = DEFAULT_PATH, source: Path | None = None, progress: Callable[[str, int, int], None] | None = None) -> int:
    """Add JMnedict to an existing dictionary built before names were supported."""
    db = sqlite3.connect(path)
    db.executescript("DROP TABLE IF EXISTS names; DROP TABLE IF EXISTS name_forms;")
    count = _build_names(db, source, progress)
    db.commit()
    db.close()
    return count


def _build_names(db: sqlite3.Connection, source: Path | None, progress: Callable[[str, int, int], None] | None = None) -> int:
    _, body = _load_xml(JMNEDICT_URL, source, "JMnedict", progress and (lambda d, t: progress("JMnedict", d, t)))
    db.executescript(
        """
        CREATE TABLE names (id INTEGER PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE name_forms (form TEXT NOT NULL, name_id INTEGER NOT NULL);
        """
    )
    count = 0
    for _, elem in ET.iterparse(io.BytesIO(body)):
        if elem.tag != "entry":
            continue
        translations = []
        for trans in elem.iter("trans"):
            types = [t.text for t in trans.iter("name_type") if t.text in NAME_TYPES]
            if types:
                translations.append({"types": types, "det": [d.text for d in trans.iter("trans_det")]})
        if translations:
            name_id = int(elem.findtext("ent_seq"))
            kanji = [k.findtext("keb") for k in elem.iter("k_ele")]
            readings = [r.findtext("reb") for r in elem.iter("r_ele")]
            data = {"kanji": kanji, "readings": readings, "trans": translations}
            db.execute("INSERT INTO names VALUES (?, ?)", (name_id, json.dumps(data, ensure_ascii=False)))
            db.executemany("INSERT INTO name_forms VALUES (?, ?)", [(f, name_id) for f in {*kanji, *readings}])
            count += 1
        elem.clear()
    db.execute("CREATE INDEX name_forms_form ON name_forms(form)")
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
        self.has_names = self._db.execute("SELECT 1 FROM sqlite_master WHERE name = 'names'").fetchone() is not None
        self._kinds: dict[str, set[str]] = {}

    def lookup_names(self, word: dict, limit: int = 3) -> list[dict]:
        """JMnedict entries for a word's exact spelling, people first, matching reading first."""
        if not self.has_names:
            return []
        rows = self._db.execute(
            "SELECT n.data FROM name_forms f JOIN names n ON n.id = f.name_id WHERE f.form = ?", (word["surface"],)
        ).fetchall()
        entries = [json.loads(data) for (data,) in rows]

        def score(entry: dict) -> int:
            types = {t for tr in entry["trans"] for t in tr["types"]}
            return 2 * (word["reading"] in entry["readings"]) + bool(types & _PERSON_TYPES)

        best = sorted(entries, key=score, reverse=True)[:limit]
        return [
            {
                "kanji": e["kanji"],
                "readings": e["readings"],
                "names": [{"types": [NAME_TYPES[t] for t in tr["types"]], "det": tr["det"]} for tr in e["trans"]],
            }
            for e in best
        ]

    def has_form(self, form: str) -> bool:
        """Whether some JMdict entry is spelled or read `form`."""
        return self._db.execute("SELECT 1 FROM forms WHERE form = ? LIMIT 1", (form,)).fetchone() is not None

    def kinds(self, form: str) -> set[str]:
        """JMdict part-of-speech codes ("n", "adv", "v5r", ...) of the entries spelled or read
        `form`; empty if there are none."""
        cached = self._kinds.get(form)
        if cached is None:
            rows = self._db.execute(
                "SELECT e.data FROM forms f JOIN entries e ON e.id = f.entry_id WHERE f.form = ?", (form,)
            ).fetchall()
            cached = {p for (data,) in rows for sense in json.loads(data)["senses"] for p in sense["pos"]}
            self._kinds[form] = cached
        return cached

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
            score += 4  # The normalized form pins the kanji even when the text is in kana (なる -> 成る)
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
