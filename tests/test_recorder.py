import json

from PIL import Image, ImageDraw

from horu import recorder


def picture(text: str) -> Image.Image:
    image = Image.new("RGB", (640, 360), (20, 20, 40))
    ImageDraw.Draw(image).text((40, 300), text, fill=(255, 255, 255))
    return image


def rows(path) -> list[dict]:
    return [json.loads(line) for line in (path / "log.jsonl").read_text(encoding="utf-8").splitlines()]


def test_a_recording_keeps_each_changed_frame_once_and_every_look(tmp_path):
    rec = recorder.Recorder("P5R.exe", root=tmp_path)
    whole = (0, 0, 640, 360)
    first, second = picture("first line"), picture("second line, longer")
    rec.look(first, whole, [], fired=False, fresh=True)
    rec.look(first, whole, [], fired=False, fresh=False)  # nothing changed: no new pictures
    rec.look(first, whole, [(0.0, 0.7, 1.0, 1.0)], fired=True, fresh=False)  # a read: the window is kept
    rec.look(second, whole, [], fired=False, fresh=False)
    rec.note("read", auto=True, lines=[{"text": "first line", "kind": "dialogue", "repeat": False, "speaker": None}])
    rec.close()

    assert rec.path.parent == tmp_path and rec.path.name.endswith("-P5R_exe")
    assert sorted(p.name for p in (rec.path / "frames").iterdir()) == ["000000.jpg", "000003.jpg"]
    # The window whole: at the first look, at the read, but not again a moment after it.
    assert sorted(p.name for p in (rec.path / "full").iterdir()) == ["000000.jpg", "000002.jpg"]
    log = rows(rec.path)
    looks = [r for r in log if "look" in r]
    assert [r["frame"] for r in looks] == [0, 0, 0, 3]
    assert [r["full"] for r in looks] == [0, None, 2, None]
    assert [r["fired"] for r in looks] == [False, False, True, False]
    assert looks[2]["regions"] == [[0.0, 0.7, 1.0, 1.0]] and looks[0]["fresh"]
    assert [r["event"] for r in log if "event" in r] == ["start", "read", "stop"]
    assert rec.bytes > 0


def test_a_recording_stops_being_written_once_closed_and_expires(tmp_path):
    rec = recorder.Recorder("game", root=tmp_path, limit=0)
    assert rec.expired
    rec.close()
    rec.note("read", auto=True, lines=[])  # late, from the reading thread: ignored
    assert rows(rec.path)[-1]["event"] == "stop"
