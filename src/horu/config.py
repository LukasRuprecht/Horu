"""Loading config.toml into typed settings."""

from __future__ import annotations

import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path("config.toml")
EXAMPLE_PATH = Path("config.example.toml")
CAPTURE_MODES = ("window", "monitor", "region")
AI_PROVIDERS = ("none", "anthropic", "openai_compat")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Region:
    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True)
class CaptureSettings:
    mode: str
    monitor: int
    region: Region | None


@dataclass(frozen=True)
class AISettings:
    provider: str = "none"
    model: str = ""
    base_url: str = ""
    api_key_env: str = ""


@dataclass(frozen=True)
class Config:
    capture: CaptureSettings
    capture_hotkey: str
    ocr_engine: str
    save_captures: bool
    captures_dir: Path
    ai: AISettings = AISettings()


def load(path: Path = DEFAULT_PATH) -> Config:
    if not path.exists():
        if path == DEFAULT_PATH and EXAMPLE_PATH.exists():
            shutil.copy(EXAMPLE_PATH, path)
            print(f"Created {path} from {EXAMPLE_PATH}.")
        else:
            raise ConfigError(f"Config file not found: {path}")

    with path.open("rb") as f:
        raw = tomllib.load(f)

    try:
        cap = raw.get("capture", {})
        mode = cap.get("mode", "window")
        if mode not in CAPTURE_MODES:
            raise ConfigError(f"capture.mode must be one of {CAPTURE_MODES}, got {mode!r}")
        region = cap.get("region")
        debug = raw.get("debug", {})
        ai = raw.get("ai", {})
        provider = ai.get("provider", "none")
        if provider not in AI_PROVIDERS:
            raise ConfigError(f"ai.provider must be one of {AI_PROVIDERS}, got {provider!r}")
        return Config(
            capture=CaptureSettings(
                mode=mode,
                monitor=int(cap.get("monitor", 1)),
                region=Region(**{k: int(v) for k, v in region.items()}) if region else None,
            ),
            capture_hotkey=raw["hotkeys"]["capture"],
            ocr_engine=raw["ocr"]["engine"],
            save_captures=bool(debug.get("save_captures", False)),
            captures_dir=Path(debug.get("captures_dir", "captures")),
            ai=AISettings(
                provider=provider,
                model=ai.get("model", ""),
                base_url=ai.get("base_url", ""),
                api_key_env=ai.get("api_key_env", ""),
            ),
        )
    except (KeyError, TypeError) as e:
        raise ConfigError(f"Invalid config in {path}: {e!r}") from e
