from horu.text_sources.vote import align, vote


def test_align_marks_substitutions_deletions_and_insertions():
    assert align("少回の声", "少女の声") == (["少", "女", "の", "声"], ["", "", "", "", ""])
    assert align("少の声", "少女の声") == (["少", "の", "声"], ["", "女", "", ""])
    assert align("少女の声", "少の声") == (["少", "", "の", "声"], ["", "", "", "", ""])


def test_majority_corrects_a_misread():
    assert vote("ジョーカー、倒しちゃつて。", ["ジョーカー、倒しちゃって。", "ジョーカー、倒しちゃって。"]) == "ジョーカー、倒しちゃって。"
    assert vote("アカジノから脱出しろ」", ["『カジノから脱出しろ」", "『カジノから脱出しろ」"]) == "『カジノから脱出しろ」"


def test_majority_inserts_a_dropped_character():
    assert vote("少の声", ["少女の声", "少女の声"]) == "少女の声"


def test_one_reader_alone_changes_nothing():
    assert vote("少回の声", ["少女の声", "少西の薬"]) == "少回の声"
    assert vote("エイガオン", ["ダウンロード", "エイガオン"]) == "エイガオン"


def test_kana_sizes_come_from_the_reading_with_more_context():
    from horu.text_sources.vote import take_kana_sizes

    assert take_kana_sizes("うつかり近づかん", "うっかり近づかん") == "うっかり近づかん"
    # Nothing else is taken: not a character invented in a gap, nor a different kanji.
    assert take_kana_sizes("体力を回復", "体力を満回復") == "体力を回復"
    assert take_kana_sizes("回復", "回複") == "回復"


def test_lines_with_a_doubtful_full_size_kana_are_voted_on():
    from horu.text_sources.rapidocr_source import _SMALL_KANA_DOUBT

    assert _SMALL_KANA_DOUBT.search("うつかり")
    assert not _SMALL_KANA_DOUBT.search("つづきから")  # a line-initial つ is never small
