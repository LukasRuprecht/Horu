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


def test_prefixes_join_the_word_after_them():
    words = analyze("インパ様のおやしきはここだが今はおいでにはならん。")
    surfaces = [w.surface for w in words]
    assert "おやしき" in surfaces and "おいで" in surfaces and "ならん" in surfaces
    yashiki, oide, naran = (words[surfaces.index(s)].to_dict() for s in ("おやしき", "おいで", "ならん"))
    # Looked up whole first, then without the prefix (JMdict has お出で but no お屋敷).
    assert yashiki["lookup_forms"][:2] == ["おやしき", "お屋敷"] and "屋敷" in yashiki["lookup_forms"]
    assert oide["pos"] == "noun" and oide["dictionary_form"] == "おいで"
    assert naran["grammar"] == ["negative 〜ん"]


def test_furigana_only_over_kanji():
    assert furigana("行った", "イッタ") == [("行", "い"), ("った", None)]
    assert furigana("取り扱い", "とりあつかい") == [("取", "と"), ("り", None), ("扱", "あつか"), ("い", None)]
    assert furigana("アパート", "アパート") == [("アパート", None)]
    assert furigana("今日", "きょう") == [("今日", "きょう")]


def test_to_hiragana():
    assert to_hiragana("ジュンペー") == "じゅんぺー"


def test_stretched_vowels_are_spelled_out():
    from horu.nlp.kana import unstretched

    assert unstretched("ねーちゃん")[0] == "ねえちゃん"
    assert unstretched("ありがとー")[0] == "ありがとう"
    assert "すごい" in unstretched("すごーい")
    assert unstretched("コーヒー") == []  # katakana is spelled with ー


def test_words_split_at_a_stretched_vowel_are_joined():
    known = {"ねえちゃん", "ありがとう", "そうだね"}.__contains__
    words = analyze("なんか　ねーちゃんに", known)
    sister = next(w for w in words if w.surface == "ねーちゃん").to_dict()
    assert sister["dictionary_form"] == "ねえちゃん" and sister["lookup_forms"] == ["ねえちゃん"]
    assert [w.surface for w in analyze("ありがとー！", known)] == ["ありがとー", "！"]
    # Grammar isn't swallowed into a set phrase.
    assert [w.surface for w in analyze("そーだね", known)] == ["そー", "だ", "ね"]
    # Without the dictionary nothing is joined.
    assert "ねーちゃん" not in [w.surface for w in analyze("なんか　ねーちゃんに")]
