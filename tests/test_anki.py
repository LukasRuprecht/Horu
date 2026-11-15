import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from horu import anki as horu_anki
from horu.anki import NOTE_TYPE, AnkiClient, AnkiError, Card, note_fields


class FakeAnkiConnect:
    """Just enough of AnkiConnect's API (version 6) to exercise Horu's client."""

    def __init__(self):
        self.models = {}
        self.decks = {"Default"}
        self.notes = []
        self.media = {}
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                assert body["version"] == 6
                try:
                    result, error = fake.handle(body["action"], body.get("params", {})), None
                except Exception as e:
                    result, error = None, str(e)
                payload = json.dumps({"result": result, "error": error}).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(payload)

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def handle(self, action, params):
        if action == "deckNames":
            return sorted(self.decks)
        if action == "modelNames":
            return list(self.models)
        if action == "createModel":
            self.models[params["modelName"]] = params
            return {}
        if action == "modelTemplates":
            return {t["Name"]: {"Front": t["Front"], "Back": t["Back"]} for t in self.models[params["modelName"]]["cardTemplates"]}
        if action == "updateModelTemplates":
            for t in self.models[params["model"]["name"]]["cardTemplates"]:
                t.update(params["model"]["templates"].get(t["Name"], {}))
            return None
        if action == "updateModelStyling":
            self.models[params["model"]["name"]]["css"] = params["model"]["css"]
            return None
        if action == "createDeck":
            self.decks.add(params["deck"])
            return 1
        if action == "storeMediaFile":
            self.media[params["filename"]] = params["data"]
            return params["filename"]
        if action == "findNotes":
            return [i for i, n in enumerate(self.notes) if f'"Word:{n["fields"]["Word"]}"' in params["query"]
                    and f'"deck:{n["deckName"]}"' in params["query"]]
        if action == "addNote":
            self.notes.append(params["note"])
            return len(self.notes)
        raise ValueError(f"unsupported action {action}")


@pytest.fixture
def anki():
    fake = FakeAnkiConnect()
    yield fake
    fake.server.shutdown()


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


def test_first_card_creates_note_type_and_deck(anki):
    client = AnkiClient(anki.url)
    assert client.add_card("Horu", card(), screenshot_png=b"\x89PNG fake") == 1
    assert NOTE_TYPE in anki.models and "Horu" in anki.decks
    [note] = anki.notes
    assert note["modelName"] == NOTE_TYPE and note["tags"] == ["horu"]
    # The word, and the line it came from as it is: nothing marked in it.
    assert note["fields"]["Sentence"] == "大きいアパートに住めますからね。"
    assert note["fields"]["Word"] == "住む"
    front = anki.models[NOTE_TYPE]["cardTemplates"][0]["Front"]
    assert front.index("{{Word}}") < front.index("{{Sentence}}") and "Screenshot" not in front
    assert "{{Screenshot}}" in anki.models[NOTE_TYPE]["cardTemplates"][0]["Back"]
    assert note["fields"]["Screenshot"].startswith('<img src="horu_')
    assert len(anki.media) == 1


def test_duplicate_word_in_deck_is_refused(anki):
    client = AnkiClient(anki.url)
    client.add_card("Horu", card())
    with pytest.raises(AnkiError, match="already in your Horu deck"):
        client.add_card("Horu", card(sentence="ここに住めますか？"))
    client.add_card("Other", card())  # another deck is fine
    assert len(anki.notes) == 2


def test_status_lists_decks_or_explains_how_to_connect(anki):
    assert AnkiClient(anki.url).status() == {"connected": True, "decks": ["Default"]}
    offline = AnkiClient("http://127.0.0.1:9", timeout=0.5).status()
    assert offline["connected"] is False and "AnkiConnect" in offline["error"]


def test_fields_escape_html():
    assert note_fields(card(sentence="<b>は</b>が", word="が"))["Sentence"] == "&lt;b&gt;は&lt;/b&gt;が"


def test_a_note_type_with_the_earlier_front_is_brought_up_to_date(anki):
    old = {"modelName": NOTE_TYPE, "css": "old", "cardTemplates": [{"Name": "Sentence", "Front": horu_anki.OLD_FRONT, "Back": "old back"}]}
    anki.models[NOTE_TYPE] = old
    AnkiClient(anki.url).add_card("Horu", card())
    assert old["cardTemplates"][0] == {"Name": "Sentence", "Front": horu_anki.FRONT, "Back": horu_anki.BACK}
    assert old["css"] == horu_anki.CSS


def test_a_front_edited_in_anki_is_left_alone(anki):
    mine = {"modelName": NOTE_TYPE, "css": "mine", "cardTemplates": [{"Name": "Sentence", "Front": "{{Word}} my way", "Back": "my back"}]}
    anki.models[NOTE_TYPE] = mine
    AnkiClient(anki.url).add_card("Horu", card())
    assert mine["cardTemplates"][0]["Front"] == "{{Word}} my way" and mine["css"] == "mine"
