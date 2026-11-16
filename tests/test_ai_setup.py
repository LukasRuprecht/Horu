import pytest

from horu import ai_setup
from horu.config import AISettings
from horu.prefs import DEFAULTS
from horu.tutor import Line, Tutor


@pytest.fixture
def keys(monkeypatch):
    """An in-memory credential store instead of the real one."""
    store = {}

    class FakeKeyring:
        class errors:
            class PasswordDeleteError(Exception):
                pass

        @staticmethod
        def get_password(service, name):
            return store.get((service, name))

        @staticmethod
        def set_password(service, name, value):
            store[(service, name)] = value

        @staticmethod
        def delete_password(service, name):
            if (service, name) not in store:
                raise FakeKeyring.errors.PasswordDeleteError()
            del store[(service, name)]

    monkeypatch.setitem(__import__("sys").modules, "keyring", FakeKeyring)
    for env in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    return store


def prefs(**values):
    return {**DEFAULTS, **values}


def test_off_by_default(keys):
    resolved = ai_setup.resolve(prefs(), AISettings())
    assert resolved.provider is None and resolved.error is None and resolved.source == "config.toml"


def test_cloud_provider_needs_a_key(keys):
    resolved = ai_setup.resolve(prefs(aiProvider="anthropic", aiModel="claude-opus-5-5"), AISettings())
    assert resolved.provider is None
    assert "API key" in resolved.error

    ai_setup.save_key("anthropic", "sk-test")
    assert ai_setup.has_saved_key("anthropic")
    resolved = ai_setup.resolve(prefs(aiProvider="anthropic", aiModel="claude-opus-5-5"), AISettings())
    assert resolved.provider.label == "Claude (claude-opus-5-5)"

    ai_setup.save_key("anthropic", "")  # clearing removes it
    assert not ai_setup.has_saved_key("anthropic")


def test_env_var_still_works(keys, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "from-env")
    assert ai_setup.get_key("openai") == "from-env"
    ai_setup.save_key("openai", "from-store")
    assert ai_setup.get_key("openai") == "from-store"  # the app's saved key wins


def test_ollama_with_separate_translation_model(keys):
    resolved = ai_setup.resolve(
        prefs(aiProvider="ollama", aiModel="gemma3:4b", translationModel="7shi/gemma-2-jpn-translate:2b-instruct-q8_0"),
        AISettings(),
    )
    assert resolved.error is None
    assert resolved.provider.label == "gemma3:4b (127.0.0.1)"
    assert resolved.translator.model == "7shi/gemma-2-jpn-translate:2b-instruct-q8_0"


def test_same_translation_model_means_no_separate_translator(keys):
    resolved = ai_setup.resolve(prefs(aiProvider="ollama", aiModel="gemma3:4b", translationModel="gemma3:4b"), AISettings())
    assert resolved.translator is None


def test_localhost_is_rewritten_to_ipv4(keys):
    resolved = ai_setup.resolve(prefs(aiProvider="custom", aiModel="m", aiBaseUrl="http://localhost:1234/v1"), AISettings())
    assert resolved.provider.base_url == "http://127.0.0.1:1234/v1"


def test_missing_model_is_reported(keys):
    assert "model" in ai_setup.resolve(prefs(aiProvider="ollama"), AISettings()).error


def test_config_toml_is_the_fallback(keys, monkeypatch):
    monkeypatch.setenv("MY_KEY", "k")
    cfg = AISettings(provider="openai_compat", model="m", base_url="https://example.com/v1", api_key_env="MY_KEY")
    resolved = ai_setup.resolve(prefs(), cfg)
    assert resolved.source == "config.toml" and resolved.provider.label == "m (example.com)"
    # Choosing a provider in the app takes over.
    assert ai_setup.resolve(prefs(aiProvider="none"), cfg).provider is None


def test_dedicated_translator_gets_a_bare_prompt():
    sent = []

    class Recorder:
        label = "rec"

        def chat(self, system, effort="low", history=None, temperature=None):
            sent.append((system, history, temperature))
            return self

        def send(self, text):
            sent.append(text)
            return iter(["ok"])

    tutor = Tutor(Recorder(), "Beginner", translator=Recorder())
    list(tutor.translate(Line("行ったら家賃も安くなる", "順平", ["「前の行」"])))
    assert sent == [
        ("", [{"role": "user", "content": "Translate Japanese to English."}, {"role": "assistant", "content": "OK"}], 0),
        "行ったら家賃も安くなる",
    ]


def test_claude_defaults_to_sonnet():
    # Translating every new line on Opus costs far more, for little difference here.
    assert ai_setup.PRESETS["anthropic"]["model"] == "claude-sonnet-5-5"
