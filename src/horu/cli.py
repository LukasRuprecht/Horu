"""Command-line entry point."""

from __future__ import annotations

import argparse
import os
import queue
import sys
import time
import traceback
from pathlib import Path

from dotenv import load_dotenv

from horu import capture, config, hotkey, layout, text_sources


def cmd_run(cfg: config.Config, debug: bool) -> None:
    from horu.app import App

    App(cfg, debug=debug).run()


def cmd_console(cfg: config.Config) -> None:
    print(f"Loading OCR ({cfg.ocr_engine})...")
    source = load_source(cfg)

    # The hotkey fires on a background thread; do the slow work on the main thread.
    requests: queue.Queue[None] = queue.Queue()
    try:
        stop_hotkey = hotkey.listen(cfg.capture_hotkey, lambda: requests.put(None))
    except hotkey.HotkeyError as e:
        sys.exit(f"{e} Pick a different hotkey in config.toml.")
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
        print(f"  saved {capture.save(image, cfg.captures_dir)}")
    start = time.perf_counter()
    blocks = layout.group_blocks(source.read(image), image)
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
    print(f"Saved {capture.save(image, cfg.captures_dir)} ({image.width}x{image.height})")


def cmd_read(cfg: config.Config, path: Path) -> None:
    from PIL import Image

    source = load_source(cfg)
    image = Image.open(path).convert("RGB")
    for block in layout.group_blocks(source.read(image), image):
        text = f"{block.speaker}: {block.text}" if block.speaker else block.text
        print(text if block.kind == "dialogue" else f"({block.kind}) {text}")


def cmd_setup() -> None:
    from horu import dictionary

    count = dictionary.build()
    print(f"Dictionary ready: {count} JMdict entries in {dictionary.DEFAULT_PATH}")


def cmd_reset_learned() -> None:
    """Forget what Horu learned while playing; settings, the dictionary, and API keys stay."""
    from horu import autocapture, corrections, speakers, translations

    learned = [
        (speakers.DEFAULT_PATH, "speaker names and their English"),
        (translations.DEFAULT_PATH, "translations kept for lines seen again"),
        (autocapture.DEFAULT_PATH, "auto-read regions"),
        (autocapture.SIZES_PATH, "each game's dialogue text size"),
        (corrections.DEFAULT_PATH, "corrections made from a line's menu"),
    ]
    for path, what in learned:
        if path.exists():
            path.unlink()
            print(f"Removed {path} ({what}).")
        else:
            print(f"Nothing to remove for {what}.")
    print("Restart Horu if it's running: it would save what it still remembers when it next learns something.")


def cmd_add_screen(bundle: Path, fixture: Path, name: str) -> None:
    from horu import debug_bundle

    entry = debug_bundle.add_to_fixture(bundle, fixture, name)
    print(f"Added {name!r} to {fixture}. Horu made these blocks of it; correct any that are wrong there:")
    for block in entry["expected"]:
        print(f"  {block['kind']}: {block['speaker'] + ': ' if block['speaker'] else ''}{block['text']}")


def cmd_monitors() -> None:
    for i, m in enumerate(capture.list_monitors()):
        label = "all monitors" if i == 0 else f"monitor {i}"
        print(f"{i}: {label}: {m['width']}x{m['height']} at ({m['left']}, {m['top']})")


def load_source(cfg: config.Config) -> text_sources.TextSource:
    try:
        return text_sources.create(cfg.ocr_engine)
    except (ImportError, ValueError) as e:
        sys.exit(str(e))


LOG_PATH = Path("data/horu.log")


def gui() -> None:
    """The Start-menu shortcut's entry point: the window, with no console. What Horu prints
    goes to data/horu.log (the run before is kept as horu.log.1), and a problem that stops it
    starting is shown in a message box. If Horu is already running, its window comes to the
    front instead."""
    if not _first_instance():
        if _show_running_window():
            return
        if not _first_instance():  # the earlier Horu ended: start in its place
            _message_box("An earlier Horu is still closing. Try again in a moment.")
            return
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log = LOG_PATH
    try:
        if LOG_PATH.exists():
            LOG_PATH.replace(LOG_PATH.with_name(LOG_PATH.name + ".1"))
    except OSError:  # still open (a Horu that is closing): keep it, log beside it
        log = LOG_PATH.with_name(f"horu-{os.getpid()}.log")
    sys.stdout = sys.stderr = log.open("w", encoding="utf-8", buffering=1)
    try:
        main()
    except SystemExit as e:
        if e.code not in (None, 0):
            _message_box(str(e.code))
        raise
    except Exception as e:
        traceback.print_exc()
        _message_box(f"Horu stopped: {e}\n\nDetails are in {LOG_PATH.resolve()}")
        raise
    # The window has closed and the work in hand is done (App.run waits for it). Python's own
    # shutdown can take seconds more (the browser control's runtime, the OCR engine's
    # threads), and a Horu started meanwhile would find this one still running: release the
    # data folder, then end now.
    _release_instance()
    sys.stdout.flush()
    os._exit(0)


_instance_lock = None  # held while Horu runs
PID_PATH = Path("data/horu.pid")


def _first_instance() -> bool:
    """Claim this data folder for this process. False if a Horu using it is already running
    (a second one would fight it for the hotkey and its files)."""
    global _instance_lock
    if sys.platform != "win32":
        return True
    import ctypes
    import hashlib

    folder = hashlib.sha1(str(Path.cwd().resolve()).lower().encode()).hexdigest()[:16]
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    lock = kernel32.CreateMutexW(None, False, f"Local\\Horu-{folder}")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        # Let go of it: holding it would keep it existing after the other Horu ends.
        kernel32.CloseHandle(ctypes.c_void_p(lock))
        return False
    _instance_lock = lock
    PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    PID_PATH.write_text(str(os.getpid()))
    return True


def _show_running_window() -> bool:
    """Bring the running Horu's window to the front, also when it's hidden in the tray, and
    return True. It may still be starting: its window gets a few seconds to appear. It may
    instead be closing (its window gone, the process not yet): then wait for it to end and
    return False, so this one can start in its place."""
    import ctypes
    from ctypes import wintypes

    try:
        pid = int(PID_PATH.read_text())
    except (OSError, ValueError):
        return False
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        title = ctypes.create_unicode_buffer(16)
        user32.GetWindowTextW(hwnd, title, 16)
        if owner.value == pid and title.value == "Horu":
            found.append(hwnd)
        return not found

    for _ in range(_WAIT_FOR_OTHER * 2):
        if not _running(pid):
            return False
        user32.EnumWindows(visit, 0)
        if found:
            hwnd = found[0]
            user32.ShowWindow(hwnd, 5)  # SW_SHOW
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            return True
        time.sleep(0.5)
    return False  # still running after all that, with no window: the caller says so


# Seconds a new start waits for an earlier Horu: for its window to appear, or for it to end.
_WAIT_FOR_OTHER = 20


def _running(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    code = wintypes.DWORD()
    ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    kernel32.CloseHandle(handle)
    return bool(ok) and code.value == 259  # STILL_ACTIVE


def _release_instance() -> None:
    """Let a new start claim the data folder: called once the window has closed, while this
    process may still take a moment to end."""
    global _instance_lock
    if _instance_lock:
        import ctypes

        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(_instance_lock))
        _instance_lock = None
    try:
        if PID_PATH.read_text().strip() == str(os.getpid()):
            PID_PATH.unlink()
    except (OSError, ValueError):
        pass


def _message_box(text: str) -> None:
    print(text)
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, "Horu", 0x10)  # MB_ICONERROR


def main() -> None:
    # Make sure Japanese prints correctly even when output is redirected on Windows.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    # API keys for AI providers live in .env (gitignored).
    load_dotenv()

    parser = argparse.ArgumentParser(prog="horu", description="Japanese study buddy for games.")
    parser.add_argument("--config", type=Path, default=config.DEFAULT_PATH, help="path to config.toml")
    sub = parser.add_subparsers(dest="command")
    parser.add_argument("--debug", action="store_true", help="open the web inspector in the app window")
    sub.add_parser("run", help="open the Horu window and listen for the hotkey (default)")
    sub.add_parser("console", help="listen for the hotkey and print text to the terminal")
    sub.add_parser("snap", help="capture once and save it as a PNG")
    read = sub.add_parser("read", help="read the Japanese text in an image file")
    read.add_argument("image", type=Path)
    sub.add_parser("monitors", help="list monitors and their coordinates")
    sub.add_parser("setup", help="download and index the JMdict dictionary")
    sub.add_parser(
        "reset-learned",
        help="forget speaker names, auto-read regions, and line corrections (keeps settings, dictionary, API keys)",
    )
    add_screen = sub.add_parser("add-screen", help="add a debug bundle's screen to a regression fixture (for developers)")
    add_screen.add_argument("bundle", type=Path, help="a horu-debug-*.zip")
    add_screen.add_argument("fixture", type=Path, help="e.g. tests/fixtures/other_games.json")
    add_screen.add_argument("name", help="the screen's name in the fixture, e.g. vn_name_box")
    args = parser.parse_args()

    if args.command == "monitors":
        cmd_monitors()
        return
    if args.command == "setup":
        cmd_setup()
        return
    if args.command == "reset-learned":
        cmd_reset_learned()
        return
    if args.command == "add-screen":
        try:
            cmd_add_screen(args.bundle, args.fixture, args.name)
        except (OSError, KeyError, ValueError) as e:
            sys.exit(str(e))
        return

    try:
        cfg = config.load(args.config)
    except config.ConfigError as e:
        sys.exit(str(e))

    if args.command == "snap":
        cmd_snap(cfg)
    elif args.command == "read":
        cmd_read(cfg, args.image)
    elif args.command == "console":
        cmd_console(cfg)
    else:
        cmd_run(cfg, args.debug)
