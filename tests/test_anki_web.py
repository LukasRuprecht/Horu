"""The AnkiWeb client's local side: cards in Horu's note type, duplicates, screenshots inside
the card, and notes waiting to sync. (Syncing itself was checked against Anki's own sync
server; it isn't run here, as that needs the `anki` package.)"""

import io

import pytest
from PIL import Image

from horu import anki
from horu.anki_web import AnkiWebClient, _unsynced_horu_notes


class Keys:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value

    def delete(self):
        self.value = ""


@pytest.fixture
def client(tmp_path):
    c = AnkiWebClient(tmp_path / "anki", keys=Keys('{"hkey": "k", "endpoint": "http://127.0.0.1:9/"}'))
    c.sync_soon = lambda delay=3.0: None  # no network in tests
    return c


def card(word="顔", form="顔"):
    return anki.Card("なんだ、その顔は。", word, form, "かお", "face", translation="What's with that face?", source="P5R")


def test_cards_go_into_horus_note_type(client):
    png = io.BytesIO()
    Image.new("RGB", (1200, 300), "red").save(png, "PNG")
    client.add_card("Japanese", card(), png.getvalue())
    assert client.has_word("Japanese", "顔") and not client.has_word("Japanese", "食う")
    with client._open() as col:
        [note] = col.find_notes(deck="Japanese")
        assert note.nt.name == anki.NOTE_TYPE and note["Sentence"] == "なんだ、その顔は。"
        # The screenshot is in the card, shrunk: no media files to sync.
        assert note["Screenshot"].startswith('<img src="data:image/jpeg;base64,') and len(note["Screenshot"]) < 40000
        assert not (client.folder / "collection.media").exists()
        # A new note type is marked to sync, as Anki does.
        assert col.q1("select usn from notetypes where name=?", anki.NOTE_TYPE) == -1


def test_a_note_type_with_the_earlier_front_is_brought_up_to_date(client):
    from fastanki._proto import notetypes_pb2
    from fastanki.schema import template_cfg

    def front(col):
        config = notetypes_pb2.Notetype.Template.Config()
        config.ParseFromString(col.q1("select config from templates where ntid=(select id from notetypes where name=?)", anki.NOTE_TYPE))
        return config.q_format

    client.add_card("Japanese", card())
    with client._open() as col:  # as an install from before the change left it, and synced
        col.con.execute("update templates set config=?, usn=0", (template_cfg(anki.OLD_FRONT, "old back"),))
        col.con.execute("update notetypes set usn=0")
    client.add_card("Japanese", card("声", "声"))
    with client._open() as col:
        assert front(col) == anki.FRONT
        assert col.q1("select usn from notetypes where name=?", anki.NOTE_TYPE) == -1  # to sync
        assert col.q1("select usn from templates") == -1
        col.con.execute("update templates set config=?, usn=0", (template_cfg("{{Word}} my way", "my back"),))
    client.add_card("Japanese", card("目", "目"))
    with client._open() as col:
        assert front(col) == "{{Word}} my way"  # edited in Anki: left alone


def test_duplicates_are_refused(client):
    client.add_card("Japanese", card())
    with pytest.raises(anki.AnkiError, match="already in your Japanese deck"):
        client.add_card("Japanese", card())
    client.add_card("Other", card())  # another deck is fine


def test_notes_not_yet_synced_are_found_to_add_again(client):
    client.add_card("Games::P5R", card())
    with client._open() as col:
        [(deck, tags, fields)] = _unsynced_horu_notes(col)
    assert deck == "Games::P5R" and tags == ["horu"] and fields["Word"] == "顔"


def test_signed_out(tmp_path):
    c = AnkiWebClient(tmp_path / "anki", keys=Keys())
    assert not c.status()["connected"] and "Sign in" in c.status()["error"]
    with pytest.raises(anki.AnkiError, match="Sign in"):
        c.add_card("Japanese", card())
