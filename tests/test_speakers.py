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


def test_a_name_the_ai_corrected_maps_its_misreads_to_it(tmp_path):
    book = SpeakerBook(tmp_path / "speakers.json")
    # OCR read the tag confidently but wrongly; the AI read it again.
    assert book.resolve("P5R.exe", "初言の男性客", score=0.92) == "初言の男性客"
    assert not book.confirmed("P5R.exe", "初言の男性客")
    book.confirm("P5R.exe", "初老の男性客", was="初言の男性客")
    assert book.confirmed("P5R.exe", "初老の男性客")
    # The same misread maps straight to the name; a dropped character, read confidently, too.
    assert book.resolve("P5R.exe", "初言の男性客", score=0.92) == "初老の男性客"
    assert book.resolve("P5R.exe", "初の男性客", score=0.92) == "初老の男性客"
    assert "初言の男性客" not in book.known("P5R.exe")


def test_a_confirmed_name_beats_a_remembered_misread(tmp_path):
    book = book_with(tmp_path, "顔色悪い男性")  # a confident misread, remembered before the AI checked
    book.confirm("P5R.exe", "顔色の悪い男性")
    assert book.resolve("P5R.exe", "顔色悪い男性", score=0.93) == "顔色の悪い男性"


def confirmed_book(tmp_path, *names):
    book = SpeakerBook(tmp_path / "speakers.json")
    for name in names:
        book.confirm("P5R.exe", name)
    return book


def test_unsure_misreads_are_guessed_as_a_confirmed_name(tmp_path):
    book = confirmed_book(tmp_path, "佐倉惣治郎", "坂本竜司")
    # Real P5R misreads of 佐倉惣治郎's tag, with their OCR scores and alternatives.
    assert book.guess("P5R.exe", "佐唐超治郎", 0.79, ("佐唐惣治郎", "佐盾超治郎")) == "佐倉惣治郎"
    assert book.guess("P5R.exe", "佐官惣治郎", 0.78, ("佐信惣治郎",)) == "佐倉惣治郎"
    assert book.guess("P5R.exe", "佐唐 惣治郎", 0.71) == "佐倉惣治郎"
    assert book.guess("P5R.exe", "佐倉\u3000惣治郎", 0.96) == "佐倉惣治郎"  # just a space
    assert book.guess("P5R.exe", "佐唐治郎", 0.82, ("佐膚惣治郎",)) == "佐倉惣治郎"
    assert book.guess("P5R.exe", "佐惣治郎", 0.91) == "佐倉惣治郎"  # confident, but characters dropped
    assert book.guess("P5R.exe", "佐唐惣治郎", 0.95, ("佐倉惣治郎",)) == "佐倉惣治郎"  # an alternative is the name


def test_confident_different_names_are_not_guessed(tmp_path):
    book = confirmed_book(tmp_path, "貞代", "少女の声")
    assert book.guess("P5R.exe", "真代", 0.96) is None
    assert book.guess("P5R.exe", "少年の声", 0.99) is None
    assert book.guess("P5R.exe", "高巻杏", 0.7) is None  # nothing close
    assert book.guess("P5R.exe", "貞代", 0.7) is None  # already the name


def test_part_of_a_name_is_not_guessed(tmp_path):
    book = confirmed_book(tmp_path, "川上貞代")
    assert book.guess("P5R.exe", "貞代", 0.75) is None  # the tag may show her given name
    assert book.guess("P5R.exe", "川上貞", 0.8) == "川上貞代"  # one character dropped


def test_guesses_need_one_clear_match(tmp_path):
    book = confirmed_book(tmp_path, "少女の声", "少年の声")
    assert book.guess("P5R.exe", "少回の声", 0.7) is None
    assert SpeakerBook(tmp_path / "other.json").guess("P5R.exe", "佐唐超治郎", 0.7) is None  # nothing confirmed
