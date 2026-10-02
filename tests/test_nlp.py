import pytest

from horu.nlp import analyze
from horu.nlp.kana import furigana, to_hiragana


def words(text):
    return {w.surface: w for w in analyze(text)}


def test_conjugations_fold_into_one_word():
    ws = words("行ったら家賃も本当に安くなるし、大きいアパートに住めますからね。")
    assert list(ws)[:3] == ["行ったら", "家賃", "も"]
    assert ws["行ったら"].dictionary_form == "行く"
    assert ws["行ったら"].grammar == ["conditional 〜たら"]
    assert ws["住めます"].dictionary_form == "住む"
    assert ws["住めます"].grammar == ["potential (can do)", "polite 〜ます"]
    assert ws["安く"].grammar == ["adverb form 〜く"]
    assert ws["アパート"].lookup_forms[0] == "アパート"


@pytest.mark.parametrize(
    "text, dictionary_form, grammar",
    [
        ("食べてしまいました", "食べる", ["completed / regret 〜てしまう", "polite 〜ます", "past 〜た"]),
        ("勉強しなければならない", "勉強する", ["must 〜なければならない"]),
        ("見られたくなかった", "見る", ["passive / potential 〜られる", "want to 〜たい", "negative 〜ない", "past 〜た"]),
        ("安くない", "安い", ["negative 〜ない"]),
    ],
)
def test_single_word_grammar(text, dictionary_form, grammar):
    [word] = analyze(text)
    assert (word.dictionary_form, word.grammar) == (dictionary_form, grammar)


def test_particles_and_copula_after_nouns_stay_separate():
    assert [w.surface for w in analyze("先輩、静かにしてください")] == ["先輩", "、", "静かに", "してください"]
    assert [w.pos for w in analyze("学生です")] == ["noun", "auxiliary"]


def test_furigana_only_over_kanji():
    assert furigana("行った", "イッタ") == [("行", "い"), ("った", None)]
    assert furigana("取り扱い", "とりあつかい") == [("取", "と"), ("り", None), ("扱", "あつか"), ("い", None)]
    assert furigana("アパート", "アパート") == [("アパート", None)]
    assert furigana("今日", "きょう") == [("今日", "きょう")]


def test_to_hiragana():
    assert to_hiragana("ジュンペー") == "じゅんぺー"
