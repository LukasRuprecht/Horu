from horu.ai_setup import PRESETS, pick_model


def test_the_newest_general_flash_model_is_picked():
    ids = ["models/gemini-2.5-flash", "models/gemini-3.6-flash", "models/gemini-3.8-flash", "models/gemini-3.8-flash-001",
           "models/gemini-3.8-flash-lite", "models/gemini-3.8-flash-tts", "models/gemini-3.8-live", "models/gemini-4-hydrogen",
           "models/gemini-3.9-flash-preview", "models/gemini-embedding-2", "models/gemini-3.8-flash-image"]
    assert pick_model(ids, "flash") == "gemini-3.8-flash"


def test_a_preview_is_used_when_there_is_nothing_else():
    assert pick_model(["models/gemini-3-flash-preview", "models/gemini-3.5-flash-lite"], "flash") == "gemini-3-flash-preview"


def test_nothing_fitting_gives_nothing():
    assert pick_model(["models/gemini-3.5-flash-lite", "models/gemini-embedding"], "flash") == ""
    assert pick_model([], "flash") == ""


def test_only_gemini_has_its_model_picked():
    assert [p for p, preset in PRESETS.items() if preset.get("suggest")] == ["gemini"]
