"""Split a Japanese sentence into learner-friendly words.

The tokenizer (Sudachi, full dictionary) splits text into morphemes: 行ったら becomes 行っ + たら
and 食べてしまいました becomes five pieces. Learners think in words, so conjugation endings and
auxiliaries are folded back onto the word they belong to, and each fold is recorded as a
grammar note ("conditional 〜たら", "polite 〜ます", ...).

Sudachi splits at its longest unit (mode C), so compounds stay whole (日本語, 作曲家); their
parts (日本 + 語) are kept for a closer look. Its part-of-speech tags are UniDic's.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache

from horu import jlpt
from horu.nlp.kana import Segment, furigana, to_hiragana, unstretched

# Part of speech (UniDic's tags, which Sudachi uses) -> simple label used by the UI (and for colouring).
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
    "ず": "negative 〜ん",
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
    # The smaller words a compound is made of (日本語: 日本, 語); empty for a single word.
    parts: tuple[Token, ...] = ()


@dataclass
class Word:
    tokens: list[Token]
    pos: str
    grammar: list[str] = field(default_factory=list)
    # The dictionary spelling of pieces joined back into one word (ねー + ちゃん -> ねえちゃん,
    # 逃げ + 切った -> 逃げ切る), and its reading when that's not the spelling itself.
    joined: str = ""
    joined_reading: str = ""

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
    def _prefix(self) -> Token | None:
        """The prefix of a prefixed word (the お of おやしき), if any."""
        first = self.tokens[0]
        return first if first.pos1 == "接頭辞" and len(self.tokens) > 1 else None

    @property
    def dictionary_form(self) -> str:
        if self.joined:
            return self.joined
        prefix = self._prefix
        if prefix:
            head = self.tokens[1]
            # お + 待ち is the noun お待ち, so a verb stem stays as written.
            return prefix.surface + (head.surface if head.pos1 == "動詞" else head.base or head.surface)
        head = self.tokens[0]
        if self.pos == "verb" and head.pos1 == "名詞":  # 勉強 + する
            return head.surface + "する"
        if _is_potential(head):
            return _strip_origin(head.lemma)
        return head.base or head.surface

    @property
    def dictionary_reading(self) -> str:
        if self.joined:
            return self.joined_reading or self.joined
        prefix = self._prefix
        if prefix:
            head = self.tokens[1]
            return to_hiragana(prefix.reading + (head.reading if head.pos1 == "動詞" else head.base_reading))
        head = self.tokens[0]
        if self.pos == "verb" and head.pos1 == "名詞":
            return to_hiragana(head.reading) + "する"
        return to_hiragana(head.lemma_reading if _is_potential(head) else head.base_reading)

    @property
    def lookup_forms(self) -> list[str]:
        """Forms to try in the dictionary, best first."""
        if self.joined:
            return [self.joined]
        prefix = self._prefix
        if prefix:
            # The whole word if the dictionary has it (おいで, お茶), else the word without
            # its prefix (屋敷 for おやしき).
            head = self.tokens[1]
            whole = [self.dictionary_form, prefix.surface + _strip_origin(head.lemma)]
            alone = [head.base or head.surface, _strip_origin(head.lemma), head.surface]
            return list(dict.fromkeys(f for f in whole + alone if f))
        head = self.tokens[0]
        forms = [self.dictionary_form, _strip_origin(head.lemma), head.surface]
        # Casual stretched spellings: そー is looked up as そう, すごーい as すごい.
        forms += [v for f in forms for v in unstretched(f)]
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
            "jlpt": None if self.pos in _NO_LEVEL else jlpt.level(self.dictionary_form, self.dictionary_reading),
            "lookup_forms": self.lookup_forms,
            "lemma": self.dictionary_form if self._prefix or self.joined else _strip_origin(self.tokens[0].lemma),
            "parts": [Word([part], _pos_label(part)).to_dict() for part in self.parts],
        }

    @property
    def parts(self) -> tuple[Token, ...]:
        """The smaller words of a compound (作曲家: 作曲, 家), for a closer look."""
        head = self._prefix and self.tokens[1] or self.tokens[0]
        return head.parts


# Grammar words: the JLPT lists' levels for these are about one use of many (の is N3 there).
_NO_LEVEL = {"symbol", "particle", "auxiliary", "conjunction", "prefix", "suffix"}

_lock = threading.RLock()  # a Sudachi tokenizer is used by one thread at a time


@lru_cache(maxsize=1)
def _tokenizer():
    from sudachipy import Dictionary

    return Dictionary(dict="full").tokenizer()


def _morphemes(text: str):
    with _lock:
        return list(_tokenizer().tokenize(text, _split_mode("C")))


@lru_cache(maxsize=4096)
def _reading_of(form: str) -> str:
    """The reading of a dictionary form (Sudachi reads the text, not its dictionary form)."""
    return "".join(m.reading_form() for m in _morphemes(form))


def tokenize(text: str) -> list[Token]:
    with _lock:
        # Sudachi reads … as three dots, leaving two empty pieces after it.
        return [_token(m) for m in _morphemes(text) if m.surface()]


def _token(m) -> Token:
    """A Sudachi morpheme as a Token."""
    surface = m.surface()
    pos = m.part_of_speech()
    base = m.dictionary_form() or surface
    lemma = m.normalized_form() or base
    reading = m.reading_form() or surface
    conjugates = pos[4] != "*"
    pieces = list(m.split(_split_mode("A"))) if not conjugates else []
    parts = tuple(_token(p) for p in pieces) if len(pieces) > 1 else ()
    return Token(
        surface=surface,
        pos1=_clean(pos[0]),
        pos2=_clean(pos[1]),
        pos3=_clean(pos[2]),
        lemma=lemma,
        base=base,
        lemma_reading=_reading_of(lemma) if pos[0] == "動詞" else "",
        base_reading=reading if base == surface else _reading_of(base),
        reading=reading,
        c_form=_clean(pos[5]),
        parts=parts,
    )


def _split_mode(name: str):
    from sudachipy import SplitMode

    return getattr(SplitMode, name)


def analyze(
    text: str,
    known: Callable[[str], bool] | None = None,
    kinds: Callable[[str], set[str]] | None = None,
) -> list[Word]:
    """Words of `text`. With the dictionary's help, words come out as a dictionary has them:
    `known` says whether it has a spelling (compounds it doesn't know are shown in their parts,
    and casual stretched words the tokenizer splits up, ねー + ちゃん, are joined back), and
    `kinds` gives a spelling's parts of speech (pieces of one word the tokenizer split apart,
    か + かわり, are joined back)."""
    tokens = tokenize(text)
    if known:
        # Sudachi's long units include phrases (太陽の歌) a dictionary won't have.
        tokens = [part for t in tokens for part in (t.parts if t.parts and not known(t.surface) else (t,))]
    words: list[Word] = []
    for token in tokens:
        if words and _attach(words[-1], token):
            continue
        if words and _takes_prefix(words[-1], token):
            # お + やしき, ご + 飯, お + いで: the prefix belongs to the word after it.
            prefixed = words[-1]
            prefixed.tokens.append(token)
            prefixed.pos = "noun" if token.pos1 == "動詞" else _pos_label(token)
            continue
        word = Word([token], _pos_label(token))
        if _is_potential(token):
            word.grammar.append("potential (can do)")
        words.append(word)
    for word in words:
        # A bare 〜く adjective (安く in 安くなる) is the adverb form.
        if word.pos == "adjective" and len(word.tokens) == 1 and word.tokens[0].c_form.startswith("連用形"):
            word.grammar.append("adverb form 〜く")
    if known:
        words = _join_stretched(words, known)
    if kinds:
        words = _join_known(words, kinds)
    return words


def _join_stretched(words: list[Word], known: Callable[[str], bool]) -> list[Word]:
    """Join a word split at a stretched ー when, spelled out, the two halves make a dictionary
    word: ねー + ちゃん is ねえちゃん (姉ちゃん), ありがと + ー is ありがとう. Particles and
    auxiliaries stay separate (そー + だ + ね is そう + だ + ね, not the phrase そうだね)."""
    out: list[Word] = []
    i = 0
    while i < len(words):
        pair = words[i : i + 2]
        text = "".join(w.surface for w in pair)
        joinable = (
            len(pair) == 2
            and "ー" in text
            # Before a suffix, ねー is the start of a word, not the particle ねえ.
            and (pair[0].pos not in ("symbol", "particle", "auxiliary") or pair[1].pos == "suffix")
            and pair[1].pos not in ("particle", "auxiliary")
            and (pair[1].pos != "symbol" or pair[1].surface == "ー")
        )
        spelled = next((v for v in unstretched(text) if known(v)), None) if joinable else None
        if spelled:
            tokens = pair[0].tokens + pair[1].tokens
            pos = "noun" if tokens[-1].pos1 in ("名詞", "接尾辞") else pair[0].pos
            out.append(Word(tokens, pos, pair[0].grammar + pair[1].grammar, joined=spelled))
            i += 2
        else:
            out.append(words[i])
            i += 1
    return out


# JMdict parts of speech that make a joined pair a word; and the label each gives it, in order
# of preference (じゃが is "but" before it's "potato").
_WORD_KINDS = [
    ("v", "verb"),
    ("adj-i", "adjective"),
    ("adj-na", "na-adjective"),
    ("adj-pn", "pre-noun"),
    ("conj", "conjunction"),
    ("adv", "adverb"),
    ("pn", "pronoun"),
    ("n", "noun"),
]
_FUNCTION = {"particle", "auxiliary"}


def _join_known(words: list[Word], kinds: Callable[[str], set[str]]) -> list[Word]:
    """Join pairs of words the tokenizer split apart that a dictionary has as one word: か +
    かわり (in 王家とかかわり, read as とか + かわり), なに + か, 彼 + ら, 逃げ + 切る. Where joins
    overlap the longer word wins (と + かかわり, not とか + かわり). Two grammar words join only
    at the start of a sentence (じゃが, but not だ + から after a noun), and expressions and
    particles (には, ことができる) are not words."""
    n = len(words)
    # best[i]: (score, words) for words[:i]; a word scores its length squared.
    best: list[tuple[int, list[Word]]] = [(0, [])]
    for i in range(1, n + 1):
        word = words[i - 1]
        score, out = best[i - 1]
        options = [(score + len(word.surface) ** 2, out + [word])]
        if i >= 2:
            joined = _join_pair(words[i - 2], word, kinds, at_start=i == 2 or words[i - 3].pos == "symbol")
            if joined:
                score, out = best[i - 2]
                options.append((score + len(joined.surface) ** 2, out + [joined]))
        best.append(max(options, key=lambda o: o[0]))
    return best[n][1]


def _join_pair(first: Word, second: Word, kinds: Callable[[str], set[str]], at_start: bool) -> Word | None:
    if "symbol" in (first.pos, second.pos) or first.grammar or first.joined or second.joined:
        return None
    if first.pos in _FUNCTION and second.pos in _FUNCTION and not at_start:
        return None
    if first.pos in _FUNCTION and (second.grammar or second.pos in _ATTACHING_HEADS):
        return None  # は + なる would be the classical verb はなる
    # A word + particle that's a noun is a homophone (そこ + に read as 底荷, ballast); the
    # words made this way are adverbs and conjunctions (ちなみに, なにか).
    allowed = _WORD_KINDS if second.pos != "particle" else [k for k in _WORD_KINDS if k[0] in ("conj", "adv")]
    for form in dict.fromkeys([first.surface + second.surface, first.surface + second.dictionary_form]):
        codes = kinds(form)
        label = next((label for code, label in allowed if any(_is_kind(c, code) for c in codes)), None)
        if label:
            reading = to_hiragana(first.reading) + (
                to_hiragana(second.reading) if form == first.surface + second.surface else second.dictionary_reading
            )
            return Word(first.tokens + second.tokens, label, second.grammar, joined=form, joined_reading=reading)
    return None


_VERB_TYPES = ("v1", "v2", "v4", "v5", "vk", "vz", "vr", "vs-i", "vs-s")


def _is_kind(code: str, kind: str) -> bool:
    """Whether a JMdict code is of a kind. Verbs come as conjugation types (v5r, v1); vi, vt
    and vs only mark transitivity or a noun taking する."""
    if kind == "v":
        return code.startswith(_VERB_TYPES)
    if kind == "adj-i":
        return code in ("adj-i", "adj-ix")
    return code == kind


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


def _takes_prefix(word: Word, token: Token) -> bool:
    """A lone prefix followed by a noun, na-adjective or verb stem (お待ち, おいで)."""
    if len(word.tokens) != 1 or word.tokens[0].pos1 != "接頭辞":
        return False
    if token.pos1 == "動詞":
        return token.c_form.startswith("連用形")
    return token.pos1 in ("名詞", "形状詞")


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
    elif lemma == "む" and token.surface == "ん":
        # ならん, 行かん: Sudachi files the casual negative 〜ん under classical む.
        word.grammar.append("negative 〜ん")
    elif lemma in _AUXILIARY_NOTES:
        word.grammar.append(_AUXILIARY_NOTES[lemma])


def _pos_label(token: Token) -> str:
    if token.pos1 == "名詞" and token.pos2 == "固有名詞":
        return "name"
    return _POS.get(token.pos1, "other")


def _is_potential(token: Token) -> bool:
    # 住める is analysed as base 住める with normalized form 住む; their readings differ.
    return token.pos1 == "動詞" and bool(token.base_reading) and token.base_reading != token.lemma_reading


def _strip_origin(lemma: str) -> str:
    # Loanword lemmas carry their origin: "アパート-apartment".
    return lemma.split("-", 1)[0] if "-" in lemma[1:] else lemma


def _clean(value) -> str:
    return "" if value in (None, "*") else str(value)
