"""Command-line entry point."""

from __future__ import annotations

import argparse
import queue
import sys
import time
from datetime import datetime
from pathlib import Path

from horu import capture, config, preprocess, text_sources


def cmd_run(cfg: config.Config) -> None:
    from pynput import keyboard

    print(f"Loading OCR ({cfg.ocr_source})...")
    source = text_sources.create(cfg.ocr_source)

    # The hotkey fires on pynput's listener thread; do the slow work on the main thread.
    requests: queue.Queue[None] = queue.Queue()
    listener = keyboard.GlobalHotKeys({cfg.capture_hotkey: lambda: requests.put(None)})
    listener.start()
    print(f"Ready. Profile '{cfg.profile.name}'. Press {cfg.capture_hotkey} to capture, Ctrl+C to quit.")

    try:
        while True:
            try:
                requests.get(timeout=0.5)  # timeout keeps Ctrl+C responsive on Windows
            except queue.Empty:
                continue
            image = capture.grab(cfg.profile.region, cfg.profile.monitor)
            if cfg.save_captures:
                print(f"  saved {save_capture(image, cfg.captures_dir)}")
            if cfg.crop_to_text:
                image = preprocess.crop_to_text(image)
            start = time.perf_counter()
            text = source.read(image)
            print(f"[{time.perf_counter() - start:.2f}s] {text}")
    except KeyboardInterrupt:
        pass
    finally:
        listener.stop()


def cmd_snap(cfg: config.Config) -> None:
    image = capture.grab(cfg.profile.region, cfg.profile.monitor)
    print(f"Saved {save_capture(image, cfg.captures_dir)} ({image.width}x{image.height})")


def cmd_monitors() -> None:
    for i, m in enumerate(capture.list_monitors()):
        label = "all monitors" if i == 0 else f"monitor {i}"
        print(f"{i}: {label}: {m['width']}x{m['height']} at ({m['left']}, {m['top']})")


def save_capture(image, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{datetime.now():%Y%m%d-%H%M%S-%f}.png"
    image.save(path)
    return path


def main() -> None:
    # Make sure Japanese prints correctly even when output is redirected on Windows.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

    parser = argparse.ArgumentParser(prog="horu", description="Japanese study buddy for games.")
    parser.add_argument("--config", type=Path, default=config.DEFAULT_PATH, help="path to config.toml")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="listen for the hotkey and OCR the capture region (default)")
    sub.add_parser("snap", help="capture the region once and save it as a PNG")
    sub.add_parser("monitors", help="list monitors and their coordinates")
    args = parser.parse_args()

    if args.command == "monitors":
        cmd_monitors()
        return

    try:
        cfg = config.load(args.config)
    except config.ConfigError as e:
        sys.exit(str(e))

    if args.command == "snap":
        cmd_snap(cfg)
    else:
        cmd_run(cfg)
