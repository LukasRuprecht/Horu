import json

import pytest

from horu.prefs import DEFAULTS, Prefs


def test_defaults_then_saved_values_persist(tmp_path):
    path = tmp_path / "prefs.json"
    prefs = Prefs(path)
    assert prefs.values == DEFAULTS
    prefs.set("textSize", "32")  # values from the page are coerced to the default's type
    prefs.set("furigana", False)
    assert Prefs(path).values == {**DEFAULTS, "textSize": 32, "furigana": False}


def test_unknown_or_corrupt_values_fall_back_to_defaults(tmp_path):
    path = tmp_path / "prefs.json"
    path.write_text(json.dumps({"textSize": "huge", "mystery": 1, "furiganaScale": 0.6}), encoding="utf-8")
    assert Prefs(path).values == {**DEFAULTS, "furiganaScale": 0.6}
    with pytest.raises(KeyError):
        Prefs(path).set("mystery", 1)


def test_concurrent_saves_do_not_collide(tmp_path):
    import threading

    prefs = Prefs(tmp_path / "prefs.json")
    threads = [threading.Thread(target=prefs.set, args=("textSize", 20 + i)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert Prefs(tmp_path / "prefs.json").values["textSize"] == prefs.values["textSize"]
