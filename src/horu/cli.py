"""Command-line entry point."""

from __future__ import annotations

import argparse
import queue
import sys
import time
from datetime import datetime
from pathlib import Path

from horu import capture, config, hotkey, layout, text_sources


def cmd_run(cfg: config.Config) -> None:
    print(f"Loading OCR ({cfg.ocr_engine})...")
    source = load_source(cfg)

    # The hotkey fires on a background thread; do the slow work on the main thread.
    requests: queue.Queue[None] = queue.Queue()
    try:
        stop_hotkey = hotkey.listen(cfg.capture_hotkey, lambda: requests.put(None))
    except hotkey.HotkeyError as e:
        sys.exit(str(e))
    print(f"Ready. Capture mode '{cfg.capture.mode}'. Press {cfg.capture_hotkey} to read text, Ctrl+C to quit.")

    try:
        while True:
            try:
                requests.get(timeout=0.5)  # timeout keeps Ctrl+C responsive on Windows
            except queue.Empty:
                continue
            read_screen(cfg, source)
    except KeyboardInterrupt:
        pass
    finally:
        stop_hotkey()


def read_screen(cfg: config.Config, source: text_sources.TextSource) -> None:
    image = capture.grab(cfg.capture)
    if cfg.save_captures:
        print(f"  saved {save_capture(image, cfg.captures_dir)}")
    start = time.perf_counter()
    blocks = layout.group_blocks(source.read(image))
    elapsed = time.perf_counter() - start
    if not blocks:
        print(f"[{elapsed:.2f}s] No Japanese text found.")
        return
    print(f"[{elapsed:.2f}s] {len(blocks)} block(s):")
    for block in blocks:
        print(f"  {block.speaker}: {block.text}" if block.speaker else f"  {block.text}")


def cmd_snap(cfg: config.Config) -> None:
    # Give the user a moment to focus the game when capturing the focused window.
    if cfg.capture.mode == "window":
        print("Capturing the focused window in 3 seconds; switch to your game...")
        time.sleep(3)
    image = capture.grab(cfg.capture)
    print(f"Saved {save_capture(image, cfg.captures_dir)} ({image.width}x{image.height})")


def cmd_read(cfg: config.Config, path: Path) -> None:
    from PIL import Image

    source = load_source(cfg)
    for block in layout.group_blocks(source.read(Image.open(path))):
        print(f"{block.speaker}: {block.text}" if block.speaker else block.text)


def cmd_monitors() -> None:
    for i, m in enumerate(capture.list_monitors()):
        label = "all monitors" if i == 0 else f"monitor {i}"
        print(f"{i}: {label}: {m['width']}x{m['height']} at ({m['left']}, {m['top']})")


def load_source(cfg: config.Config) -> text_sources.TextSource:
    try:
        return text_sources.create(cfg.ocr_engine)
    except (ImportError, ValueError) as e:
        sys.exit(str(e))


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
    sub.add_parser("run", help="listen for the hotkey and read text on screen (default)")
    sub.add_parser("snap", help="capture once and save it as a PNG")
    read = sub.add_parser("read", help="read the Japanese text in an image file")
    read.add_argument("image", type=Path)
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
    elif args.command == "read":
        cmd_read(cfg, args.image)
    else:
        cmd_run(cfg)
