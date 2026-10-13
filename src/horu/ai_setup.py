"""AI provider setup from the settings panel: presets, API keys, and Ollama models.

Non-secret choices (provider, model, server address) are saved with the other preferences.
API keys are saved in the operating system's credential store (Windows Credential Manager)
via `keyring`, never in a plain file. For people who prefer files, the [ai] section of
config.toml and keys in .env still work when nothing has been chosen in the app.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse

from horu import config, llm

KEYRING_SERVICE = "Horu"
# 127.0.0.1 rather than localhost: on Windows, "localhost" tries IPv6 first, which Ollama
# doesn't listen on, adding about two seconds to every request.
OLLAMA_URL = "http://127.0.0.1:11434/v1"
OLLAMA_DOWNLOAD = "https://ollama.com/download"

# Each preset: the provider type, server address, where keys come from, and a default model.
# Models are left blank where the right choice changes too often to hard-code.
PRESETS: dict[str, dict] = {
    "none": {"label": "Off"},
    "anthropic": {
        "label": "Claude",
        "kind": "anthropic",
        "key_env": "ANTHROPIC_API_KEY",
        "model": "claude-opus-5-5",
        "key_url": "https://console.anthropic.com/settings/keys",
    },
    "openai": {
        "label": "OpenAI",
        "kind": "openai_compat",
        "base_url": "https://api.openai.com/v1",
        "key_env": "OPENAI_API_KEY",
        "key_url": "https://platform.openai.com/api-keys",
    },
    "gemini": {
        "label": "Google Gemini",
        "kind": "openai_compat",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "key_env": "GEMINI_API_KEY",
        "key_url": "https://aistudio.google.com/apikey",
    },
    "deepseek": {
        "label": "DeepSeek",
        "kind": "openai_compat",
        "base_url": "https://api.deepseek.com",
        "key_env": "DEEPSEEK_API_KEY",
        "key_url": "https://platform.deepseek.com/api_keys",
    },
    "ollama": {"label": "Ollama (free, runs on this PC)", "kind": "openai_compat", "base_url": OLLAMA_URL},
    "custom": {"label": "Other OpenAI-compatible server", "kind": "openai_compat", "needs_key": "optional"},
}

# Suggested local models, grouped by what they're for. Sizes are download sizes. The notes
# reflect testing with Horu's own prompts on Persona dialogue.
OLLAMA_SUGGESTIONS = [
    {
        "name": "7shi/gemma-2-jpn-translate:2b-instruct-q8_0",
        "size": "3.3 GB",
        "use": "translation",
        "note": "Recommended for translations: fast and good for its size. Translation only; it can't explain grammar.",
        "url": "https://ollama.com/7shi/gemma-2-jpn-translate",
    },
    {
        "name": "qwen3:14b",
        "size": "9.3 GB",
        "use": "explain",
        "note": "Recommended for explanations if your graphics card has 12 GB+ of memory. Thinks before answering, so replies start a few seconds later.",
        "url": "https://ollama.com/library/qwen3",
    },
    {
        "name": "gemma3:4b",
        "size": "3.3 GB",
        "use": "explain",
        "note": "Runs on most PCs and answers quickly in clear English, but in testing it often got grammar wrong. Check it against the word popup.",
        "url": "https://ollama.com/library/gemma3",
    },
    {
        "name": "qwen3:4b",
        "size": "2.5 GB",
        "use": "explain",
        "note": "Runs on most PCs; Qwen models are strong at Japanese for their size. Thinks before answering.",
        "url": "https://ollama.com/library/qwen3",
    },
    {
        "name": "gemma3:1b",
        "size": "0.8 GB",
        "use": "explain",
        "note": "Tiny; runs almost anywhere. Expect basic, sometimes wrong explanations.",
        "url": "https://ollama.com/library/gemma3",
    },
    {
        "name": "llama3.2:1b",
        "size": "1.3 GB",
        "use": "explain",
        "note": "Not recommended: Japanese isn't one of its supported languages, and in testing its explanations came out garbled.",
        "url": "https://ollama.com/library/llama3.2",
    },
]


@dataclass
class Resolved:
    """The working providers built from the current settings."""

    provider: llm.Provider | None = None
    translator: llm.Provider | None = None  # a separate translation model, if one is set
    error: str | None = None
    source: str = "settings"  # "settings" or "config.toml"


# --- API keys --------------------------------------------------------------------------


def get_key(preset: str) -> str:
    """The saved key for a preset, falling back to its environment variable (.env)."""
    try:
        import keyring

        key = keyring.get_password(KEYRING_SERVICE, preset)
    except Exception:  # no usable credential store on this system
        key = None
    if key:
        return key
    env = PRESETS.get(preset, {}).get("key_env")
    return os.environ.get(env, "") if env else ""


def has_saved_key(preset: str) -> bool:
    try:
        import keyring

        return keyring.get_password(KEYRING_SERVICE, preset) is not None
    except Exception:
        return False


def save_key(preset: str, key: str) -> None:
    import keyring

    if key:
        keyring.set_password(KEYRING_SERVICE, preset, key)
    else:
        try:
            keyring.delete_password(KEYRING_SERVICE, preset)
        except keyring.errors.PasswordDeleteError:
            pass  # nothing saved


# --- Building providers ----------------------------------------------------------------


def settings_from_config(ai: config.AISettings) -> dict:
    """Translate config.toml's [ai] section into the settings-panel shape."""
    if ai.provider == "anthropic":
        return {"aiProvider": "anthropic", "aiModel": ai.model, "aiBaseUrl": "", "_keyEnv": "ANTHROPIC_API_KEY"}
    if ai.provider == "openai_compat":
        return {"aiProvider": "custom", "aiModel": ai.model, "aiBaseUrl": ai.base_url, "_keyEnv": ai.api_key_env}
    return {"aiProvider": "none"}


def resolve(values: dict, fallback: config.AISettings) -> Resolved:
    """Build providers from the saved preferences, or from config.toml if none are chosen."""
    source = "settings"
    if not values.get("aiProvider"):
        values = {**values, **settings_from_config(fallback)}
        source = "config.toml"
    preset_id = values.get("aiProvider", "none")
    preset = PRESETS.get(preset_id)
    if preset is None:
        return Resolved(error=f"Unknown AI provider {preset_id!r}.", source=source)
    if preset_id == "none":
        return Resolved(source=source)

    base_url = _prefer_ipv4(preset.get("base_url") or values.get("aiBaseUrl", ""))
    key = get_key(preset_id)
    if not key and values.get("_keyEnv"):
        key = os.environ.get(values["_keyEnv"], "")
    if preset.get("key_env") and not key:
        return Resolved(error=f"Add your {preset['label']} API key to use it.", source=source)

    try:
        provider = llm.create(preset["kind"], values.get("aiModel", "").strip(), base_url, key)
        translator = None
        translation_model = values.get("translationModel", "").strip()
        if translation_model and translation_model != values.get("aiModel", "").strip():
            translator = llm.create(preset["kind"], translation_model, base_url, key)
    except llm.AIError as e:
        return Resolved(error=str(e), source=source)
    return Resolved(provider, translator, source=source)


def _prefer_ipv4(url: str) -> str:
    return url.replace("://localhost:", "://127.0.0.1:").replace("://localhost/", "://127.0.0.1/")


def test(provider: llm.Provider) -> str:
    """Send a tiny request; returns the reply or raises AIError."""
    reply = "".join(provider.chat("").send("Reply with just the word OK."))
    return reply.strip()[:80] or "(empty reply)"


# --- Ollama ----------------------------------------------------------------------------


def _ollama_root(base_url: str = OLLAMA_URL) -> str:
    """Ollama's own API lives beside its OpenAI-compatible /v1 endpoint."""
    parsed = urlparse(base_url)
    return f"{parsed.scheme}://{parsed.netloc}"


def ollama_models(base_url: str = OLLAMA_URL) -> dict:
    """Installed Ollama models, or running=False if Ollama isn't reachable."""
    try:
        with urllib.request.urlopen(f"{_ollama_root(base_url)}/api/tags", timeout=3) as response:
            data = json.load(response)
    except (urllib.error.URLError, OSError, ValueError):
        return {"running": False, "models": [], "download": OLLAMA_DOWNLOAD}
    models = [{"name": m["name"], "size": m.get("size", 0)} for m in data.get("models", [])]
    return {"running": True, "models": sorted(models, key=lambda m: m["name"]), "download": OLLAMA_DOWNLOAD}


def ollama_pull(name: str, progress: Callable[[dict], None], base_url: str = OLLAMA_URL) -> None:
    """Download a model through Ollama, reporting {status, completed, total} as it goes."""
    request = urllib.request.Request(
        f"{_ollama_root(base_url)}/api/pull",
        data=json.dumps({"model": name, "stream": True}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            for raw in response:  # one JSON object per line
                if not raw.strip():
                    continue
                event = json.loads(raw)
                if "error" in event:
                    raise llm.AIError(f"Ollama couldn't download {name}: {event['error']}")
                progress(
                    {
                        "status": event.get("status", ""),
                        "completed": event.get("completed", 0),
                        "total": event.get("total", 0),
                    }
                )
    except urllib.error.URLError as e:
        raise llm.AIError("Couldn't reach Ollama. Is it running?") from e
