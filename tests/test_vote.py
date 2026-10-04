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
