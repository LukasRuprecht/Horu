from horu.speakers import SpeakerBook


def book_with(tmp_path, *names):
    book = SpeakerBook(tmp_path / "speakers.json")
    for name in names:
        assert book.resolve("P5R.exe", name, score=1.0) == name
    return book


def test_dropped_character_is_corrected(tmp_path):
    book = book_with(tmp_path, "少女の声")
    assert book.resolve("P5R.exe", "少の声", score=0.96) == "少女の声"


def test_unsure_substitution_is_corrected(tmp_path):
    book = book_with(tmp_path, "少女の声")
    assert book.resolve("P5R.exe", "少回の声", score=0.76) == "少女の声"


def test_confident_different_name_is_kept(tmp_path):
    book = book_with(tmp_path, "少女の声")
    # One character apart, but read confidently: a different speaker, not a misread.
    assert book.resolve("P5R.exe", "少年の声", score=0.99) == "少年の声"


def test_ambiguous_match_is_left_alone(tmp_path):
    book = book_with(tmp_path, "少女の声", "少年の声")
    assert book.resolve("P5R.exe", "少回の声", score=0.7) == "少回の声"


def test_unsure_reads_are_not_remembered(tmp_path):
    book = SpeakerBook(tmp_path / "speakers.json")
    book.resolve("P5R.exe", "少回の声", score=0.7)
    assert book.resolve("P5R.exe", "少女の声", score=0.7) == "少女の声"  # not "corrected" to the misread


def test_games_are_separate_and_english_persists(tmp_path):
    path = tmp_path / "speakers.json"
    book = book_with(tmp_path, "謎の通信")
    book.set_english("P5R.exe", "謎の通信", "Mysterious transmission", "ai")
    reloaded = SpeakerBook(path)
    assert reloaded.english("P5R.exe", "謎の通信") == "Mysterious transmission"
    assert reloaded.english("P3R.exe", "謎の通信") is None
    assert reloaded.resolve("P3R.exe", "少の声", score=1.0) == "少の声"


def test_translations_alone_do_not_make_a_name_known(tmp_path):
    book = SpeakerBook(tmp_path / "speakers.json")
    book.set_english("P5R.exe", "少女の声", "Girl's voice", "ai")  # read unsure, never remembered
    assert book.resolve("P5R.exe", "少の声", score=0.96) == "少の声"
