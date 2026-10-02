"""Loading config.toml into typed settings."""

from __future__ import annotations

import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path("config.toml")
EXAMPLE_PATH = Path("config.example.toml")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Region:
    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True)
class Profile:
    name: str
    monitor: int
    region: Region


@dataclass(frozen=True)
class Config:
    profile: Profile
    capture_hotkey: str
    ocr_source: str
    crop_to_text: bool
    save_captures: bool
    captures_dir: Path


def load(path: Path = DEFAULT_PATH) -> Config:
    if not path.exists():
        if path == DEFAULT_PATH and EXAMPLE_PATH.exists():
            shutil.copy(EXAMPLE_PATH, path)
            print(f"Created {path} from {EXAMPLE_PATH}. Edit it to match your game.")
        else:
            raise ConfigError(f"Config file not found: {path}")

    with path.open("rb") as f:
        raw = tomllib.load(f)

    try:
        profile_name = raw["active_profile"]
        p = raw["profiles"][profile_name]
        profile = Profile(
            name=profile_name,
            monitor=int(p.get("monitor", 1)),
            region=Region(**{k: int(v) for k, v in p["region"].items()}),
        )
        return Config(
            profile=profile,
            capture_hotkey=raw["hotkeys"]["capture"],
            ocr_source=raw["ocr"]["source"],
            crop_to_text=bool(raw["ocr"].get("crop_to_text", True)),
            save_captures=bool(raw.get("debug", {}).get("save_captures", False)),
            captures_dir=Path(raw.get("debug", {}).get("captures_dir", "captures")),
        )
    except (KeyError, TypeError) as e:
        raise ConfigError(f"Invalid config in {path}: {e!r}") from e
