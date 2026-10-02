"""Split a Japanese sentence into learner-friendly words.

The tokenizer (fugashi + UniDic) splits text into morphemes: 行ったら becomes 行っ + たら and
食べてしまいました becomes five pieces. Learners think in words, so conjugation endings and
auxiliaries are folded back onto the word they belong to, and each fold is recorded as a
grammar note ("conditional 〜たら", "polite 〜ます", ...).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from horu.nlp.kana import Segment, furigana, to_hiragana

# UniDic part of speech -> simple label used by the UI (and for colouring).
_POS = {
    "名詞": "noun",
    "代名詞": "pronoun",
    "動詞": "verb",
    "形容詞": "adjective",
    "形状詞": "na-adjective",
    "副詞": "adverb",
    "助詞": "particle",
    "助動詞": "auxiliary",
    "連体詞": "pre-noun",
    "接続詞": "conjunction",
    "感動詞": "interjection",
    "接頭辞": "prefix",
    "接尾辞": "suffix",
    "補助記号": "symbol",
    "記号": "symbol",
    "空白": "symbol",
}

# Particles that are part of a conjugation rather than words of their own.
_INFLECTING_PARTICLES = {"て", "で", "ば", "たり", "だり", "ちゃ", "じゃ"}

# Helper verbs that attach after the て-form, with their grammar notes.
_TE_HELPERS = {
    "居る": "ongoing / state 〜ている",
    "仕舞う": "completed / regret 〜てしまう",
    "置く": "in advance 〜ておく",
    "見る": "try doing 〜てみる",
    "下さる": "please 〜てください",
    "呉れる": "for me 〜てくれる",
    "上げる": "for someone 〜てあげる",
    "貰う": "receive a favour 〜てもらう",
    "有る": "resulting state 〜てある",
    "行く": "going on 〜ていく",
    "来る": "coming about 〜てくる",
}

_AUXILIARY_NOTES = {
    "ます": "polite 〜ます",
    "たい": "want to 〜たい",
    "れる": "passive / potential 〜れる",
    "られる": "passive / potential 〜られる",
    "せる": "causative 〜せる",
    "させる": "causative 〜させる",
    "う": "volitional 〜う",
    "よう": "volitional 〜よう",
    "ぬ": "negative 〜ん",
    "ない": "negative 〜ない",
}

_ATTACHING_HEADS = {"verb", "adjective", "na-adjective"}


@dataclass(frozen=True)
class Token:
    surface: str
    pos1: str
    pos2: str
    pos3: str
    lemma: str  # normalized dictionary form, e.g. 成る even when written なる
    base: str  # dictionary form as usually written, e.g. なる
    lemma_reading: str
    base_reading: str
    reading: str
    c_form: str


@dataclass
class Word:
    tokens: list[Token]
    pos: str
    grammar: list[str] = field(default_factory=list)

    @property
    def surface(self) -> str:
        return "".join(t.surface for t in self.tokens)

    @property
    def reading(self) -> str:
        return "".join(to_hiragana(t.reading) for t in self.tokens)

    @property
    def furigana(self) -> list[Segment]:
        segments: list[Segment] = []
        for t in self.tokens:
            segments.extend(furigana(t.surface, t.reading))
        return segments

    @property
    def dictionary_form(self) -> str:
        head = self.tokens[0]
        if self.pos == "verb" and head.pos1 == "名詞":  # 勉強 + する
            return head.surface + "する"
        if _is_potential(head):
            return _strip_origin(head.lemma)
        return head.base or head.surface

    @property
    def dictionary_reading(self) -> str:
        head = self.tokens[0]
        if self.pos == "verb" and head.pos1 == "名詞":
            return to_hiragana(head.reading) + "する"
        return to_hiragana(head.lemma_reading if _is_potential(head) else head.base_reading)

    @property
    def lookup_forms(self) -> list[str]:
        """Forms to try in the dictionary, best first."""
        head = self.tokens[0]
        forms = [self.dictionary_form, _strip_origin(head.lemma), head.surface]
        return list(dict.fromkeys(f for f in forms if f))

    def to_dict(self) -> dict:
        return {
            "surface": self.surface,
            "reading": self.reading,
            "furigana": self.furigana,
            "pos": self.pos,
            "grammar": self.grammar,
            "dictionary_form": self.dictionary_form,
            "dictionary_reading": self.dictionary_reading,
            "lookup_forms": self.lookup_forms,
            "lemma": _strip_origin(self.tokens[0].lemma),
        }


@lru_cache(maxsize=1)
def _tagger():
    from fugashi import Tagger

    return Tagger()


def tokenize(text: str) -> list[Token]:
    tokens = []
    for w in _tagger()(text):
        f = w.feature
        tokens.append(
            Token(
                surface=w.surface,
                pos1=f.pos1 or "",
                pos2=f.pos2 or "",
                pos3=f.pos3 or "",
                lemma=_clean(f.lemma) or w.surface,
                base=_clean(f.orthBase) or w.surface,
                lemma_reading=_clean(f.lForm),
                base_reading=_clean(f.kanaBase),
                reading=_clean(f.kana),
                c_form=_clean(f.cForm),
            )
        )
    return tokens


def analyze(text: str) -> list[Word]:
    words: list[Word] = []
    for token in tokenize(text):
        if words and _attach(words[-1], token):
            continue
        word = Word([token], _pos_label(token))
        if _is_potential(token):
            word.grammar.append("potential (can do)")
        words.append(word)
    for word in words:
        # A bare 〜く adjective (安く in 安くなる) is the adverb form.
        if word.pos == "adjective" and len(word.tokens) == 1 and word.tokens[0].c_form.startswith("連用形"):
            word.grammar.append("adverb form 〜く")
    return words


def _attach(word: Word, token: Token) -> bool:
    """Fold `token` into `word` if it is part of its conjugation. Returns True if folded."""
    prev = word.tokens[-1]

    # 勉強 + する -> 勉強する
    if word.pos == "noun" and len(word.tokens) == 1 and "サ変可能" in prev.pos3 and token.lemma == "為る":
        word.tokens.append(token)
        word.pos = "verb"
        return True

    if word.pos not in _ATTACHING_HEADS:
        return False

    if token.pos1 == "助動詞":
        if word.pos == "na-adjective" and token.lemma != "だ":
            return False
        word.tokens.append(token)
        _note_auxiliary(word, token)
        return True

    if token.pos1 == "助詞" and token.pos2 == "接続助詞" and token.surface in _INFLECTING_PARTICLES:
        word.tokens.append(token)
        if token.surface in ("て", "で"):
            word.grammar.append("te-form 〜て")
        elif token.surface == "ば":
            if word.grammar and word.grammar[-1].startswith("negative"):
                word.grammar[-1] = "if not 〜なければ"
            else:
                word.grammar.append("conditional 〜ば")
        else:
            word.grammar.append(f"〜{token.surface}")
        return True

    # 〜てしまう, 〜ている, 〜てください, ...
    if token.pos1 == "動詞" and prev.surface in ("て", "で") and token.lemma in _TE_HELPERS:
        word.tokens.append(token)
        word.grammar[-1] = _TE_HELPERS[token.lemma]
        return True

    # 〜なければならない: the なる after 〜なければ
    if token.lemma == "成る" and prev.surface == "ば" and word.grammar and word.grammar[-1] == "if not 〜なければ":
        word.tokens.append(token)
        word.grammar[-1] = "must 〜なければならない"
        return True

    # ない as an adjective after a verb or adjective stem: 安くない, 見たくなかった
    if token.pos1 == "形容詞" and token.pos2 == "非自立可能" and token.lemma == "無い":
        word.tokens.append(token)
        if not (word.grammar and word.grammar[-1].startswith("must")):
            word.grammar.append("negative 〜ない")
        return True

    return False


def _note_auxiliary(word: Word, token: Token) -> None:
    lemma = token.lemma
    if lemma == "た":
        word.grammar.append("conditional 〜たら" if token.c_form.startswith("仮定形") else "past 〜た")
    elif lemma == "だ" and word.pos == "na-adjective":
        if token.c_form.startswith("連用形-ニ"):
            word.grammar.append("adverb 〜に")
        elif token.c_form.startswith("連体形"):
            word.grammar.append("before a noun 〜な")
    elif lemma == "ない" and word.grammar and word.grammar[-1].startswith("must"):
        pass  # the final ない of 〜なければならない
    elif lemma in _AUXILIARY_NOTES:
        word.grammar.append(_AUXILIARY_NOTES[lemma])


def _pos_label(token: Token) -> str:
    if token.pos1 == "名詞" and token.pos2 == "固有名詞":
        return "name"
    return _POS.get(token.pos1, "other")


def _is_potential(token: Token) -> bool:
    # UniDic analyses 住める as lemma 住む with base 住める; their readings differ.
    return token.pos1 == "動詞" and bool(token.base_reading) and token.base_reading != token.lemma_reading


def _strip_origin(lemma: str) -> str:
    # Loanword lemmas carry their origin: "アパート-apartment".
    return lemma.split("-", 1)[0] if "-" in lemma[1:] else lemma


def _clean(value) -> str:
    return "" if value in (None, "*") else str(value)
