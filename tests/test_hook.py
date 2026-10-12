import threading

import pytest

from horu.text_sources import hook


@pytest.mark.parametrize(
    "raw, text, speaker",
    [
        ("おはよう。", "おはよう。", None),
        ("「おはよう」", "「おはよう」", None),
        ("竜司「おはよう」", "「おはよう」", "竜司"),
        ("竜司\n「おはよう」", "「おはよう」", "竜司"),
        ("【モルガナ】ワガハイは猫じゃない！", "ワガハイは猫じゃない！", "モルガナ"),
        ("【???】「誰だ？」", "「誰だ？」", "???"),
        # A quote inside a sentence isn't a speaker.
        ("ちょっと「待って」と言った", "ちょっと「待って」と言った", None),
        # Lines wrapped to fit the text box, and the indent VNs put before narration.
        ("　雨が降っていた。\r\n傘はない。", "雨が降っていた。傘はない。", None),
        ("今日は\n　いい天気", "今日はいい天気", None),
    ],
)
def test_parse(raw, text, speaker):
    assert hook.parse(raw) == hook.Hooked(text, speaker)


@pytest.mark.parametrize("raw", ["", "   ", "Hello there", "x" * 10 + "あ" * 500, "【竜司】..."])
def test_parse_skips_what_isnt_game_text(raw):
    assert hook.parse(raw) is None


def test_websocket_lines_arrive_and_listener_waits_for_the_hooker():
    from websockets.sync.server import serve

    got, states = [], []
    arrived = threading.Event()

    def on_text(text):
        got.append(text)
        if len(got) == 2:
            arrived.set()

    def handler(socket):
        socket.send("竜司「おはよう」")
        socket.send("今日は\nいい天気".encode())
        socket.recv()  # until the client goes

    with serve(handler, "127.0.0.1", 0) as server:
        port = server.socket.getsockname()[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        listener = hook.WebSocketListener(on_text, lambda *s: states.append(s), f"ws://127.0.0.1:{port}")
        listener.start()
        assert arrived.wait(5)
        listener.stop()
        server.shutdown()
    assert got == ["竜司「おはよう」", "今日は\nいい天気"]
    assert states[0][0] == "listening"


def test_websocket_listener_reports_no_hooker():
    states = []
    reported = threading.Event()
    listener = hook.WebSocketListener(lambda t: None, lambda *s: (states.append(s), reported.set()), "ws://127.0.0.1:9")
    listener.start()
    assert reported.wait(5)
    listener.stop()
    assert states[0][0] == "waiting" and "ws://127.0.0.1:9" in states[0][1]
