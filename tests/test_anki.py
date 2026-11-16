from horu import anki
from horu.anki import Card, note_fields


def card(**overrides):
    values = dict(
        sentence="大きいアパートに住めますからね。",
        word="住めます",
        dictionary_form="住む",
        reading="すむ",
        meaning="to live; to reside",
        notes="potential (can do), polite 〜ます",
        translation="You can live in a big apartment.",
        source="P5R.exe",
    )
    return Card(**{**values, **overrides})


def test_the_front_is_the_word_then_its_line_and_the_screenshot_is_on_the_back():
    fields = note_fields(card(), '<img src="shot.jpg">')
    # The word, and the line it came from as it is: nothing marked in it.
    assert fields["Word"] == "住む" and fields["Sentence"] == "大きいアパートに住めますからね。"
    assert anki.FRONT.index("{{Word}}") < anki.FRONT.index("{{Sentence}}") and "Screenshot" not in anki.FRONT
    assert "{{Screenshot}}" in anki.BACK and fields["Screenshot"] == '<img src="shot.jpg">'


def test_fields_escape_html():
    assert note_fields(card(sentence="<b>は</b>が", word="が"))["Sentence"] == "&lt;b&gt;は&lt;/b&gt;が"
