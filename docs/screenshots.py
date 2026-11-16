"""Takes the quick start guide's screenshots (docs/images/qs-*.png) and the README's top
picture (docs/images/hero.png) from the real app.

    uv run python docs/screenshots.py              # all of them
    uv run python docs/screenshots.py hero popup   # only these (names without "qs-")

Runs Horu from source in a scratch folder (your own data isn't touched) and has it read a
stand-in game screen: tests/fixtures/screen_line.png, a made-up scene drawn by
tests/fixtures/make_screens.py, so no real game is shown. The page is driven through its
own script, and Horu's window is captured at each step (as Windows renders it, so other
windows in front don't matter). Numbered markers are drawn on the
buttons the guide explains, at the positions the page reports for them.

Needs an AI provider for the translation and explanation shots: config.toml's [ai] section
in the scratch folder is filled from the HORU_SHOTS_PROVIDER environment (base_url, model,
key variable), or the shots are taken without AI.
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "images"
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

ACCENT = (245, 192, 66)


def window_picture(hwnd: int) -> Image.Image:
    """A window's client area as Windows renders it, even if other windows cover it
    (PrintWindow with PW_RENDERFULLCONTENT, which also draws the browser control's page)."""
    from ctypes import wintypes

    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    width, height = rect.right, rect.bottom
    window_dc = user32.GetDC(hwnd)
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    gdi32.SelectObject(memory_dc, bitmap)
    user32.PrintWindow(hwnd, memory_dc, 3)  # PW_CLIENTONLY | PW_RENDERFULLCONTENT

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                    ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    header = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
    buffer = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(memory_dc, bitmap, 0, height, buffer, ctypes.byref(header), 0)
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(memory_dc)
    user32.ReleaseDC(hwnd, window_dc)
    return Image.frombuffer("RGB", (width, height), buffer, "raw", "BGRX", 0, 1)


def game_picture() -> Image.Image:
    """The stand-in game screen at a game's size: a made-up scene, no real game."""
    scene = Image.open(ROOT / "tests" / "fixtures" / "screen_line.png").convert("RGB")
    return scene.resize((1920, 1080), Image.LANCZOS)


def mark(image: Image.Image, spots: list[tuple[int, float, float]]) -> Image.Image:
    """Numbered circles at (number, x, y) in image pixels."""
    image = image.convert("RGB")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 15)
    for number, x, y in spots:
        r = 11
        draw.ellipse((x - r, y - r, x + r, y + r), fill=ACCENT, outline=(20, 20, 20), width=2)
        draw.text((x, y), str(number), font=font, fill=(20, 20, 20), anchor="mm")
    return image


def hero(window: Image.Image, game: Image.Image) -> Image.Image:
    """The made-up game scene and Horu's window side by side, on a dark backdrop."""
    height = window.height
    game = game.resize((round(game.width * height / game.height), height), Image.LANCZOS)
    pad, gap = 28, 28
    canvas = Image.new("RGB", (pad + game.width + gap + window.width + pad, pad + height + pad), (11, 12, 16))
    for picture, x in ((game, pad), (window, pad + game.width + gap)):
        ImageDraw.Draw(canvas).rectangle((x - 1, pad - 1, x + picture.width, pad + height), outline=(52, 56, 66))
        canvas.paste(picture, (x, pad))
    return canvas


def main() -> None:
    only = set(sys.argv[1:])  # names to take; all if none
    home = Path(tempfile.mkdtemp(prefix="horu-shots-"))
    (home / "data").mkdir()
    dictionary = Path(os.environ["LOCALAPPDATA"]) / "Horu" / "data" / "jmdict.sqlite"
    if dictionary.exists():
        shutil.copy(dictionary, home / "data")
    (home / "data" / "ui_prefs.json").write_text(json.dumps({"colorMode": "dark", "autoTipSeen": True}), encoding="utf-8")
    config = (ROOT / "config.example.toml").read_text(encoding="utf-8")
    provider = os.environ.get("HORU_SHOTS_PROVIDER")  # "base_url|model|KEY_VARIABLE"
    if provider:
        base_url, model, key_env = provider.split("|")
        config = config.replace('provider = "none"', 'provider = "openai_compat"').replace('model = "claude-opus-5-5"', f'model = "{model}"')
        config = config.replace('# base_url = ""', f'base_url = "{base_url}"').replace('# api_key_env = ""', f'api_key_env = "{key_env}"')
    (home / "config.toml").write_text(config, encoding="utf-8")
    sys.argv = [str(Path(__file__).resolve())]  # pywebview resolves paths from the script's
    os.chdir(home)

    from horu import app as horu_app, cli

    made = []
    original = horu_app.App.__init__

    def init(self, *a, **k):
        original(self, *a, **k)
        made.append(self)

    horu_app.App.__init__ = init

    def drive() -> None:
        try:
            while not made or made[0]._source is None or not made[0]._page_loaded.is_set():
                time.sleep(0.5)
            app = made[0]
            time.sleep(2)
            js = app.window.evaluate_js
            hwnd = app.window.native.Handle.ToInt32()
            time.sleep(1)
            dpr = js("window.devicePixelRatio") or 1

            def rect(selector: str) -> list | None:
                return js(f"(() => {{ const e = document.querySelector({json.dumps(selector)}); if (!e) return null;"
                          f" const r = e.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; }})()")

            def shot(name: str, marks: list[tuple[int, str]] = (), where: str = "below", crop_to: str | None = None, pad: tuple = (10, 10, 10, 10)) -> None:
                """Marks go just below (or above) each button, not over it."""
                if only and name not in only:
                    return
                time.sleep(0.8)
                image = window_picture(hwnd)
                spots = []
                for number, selector in marks:
                    box = rect(selector)
                    if box:
                        left, top, right, bottom = (v * dpr for v in box)
                        spots.append((number, (left + right) / 2, bottom + 13 if where == "below" else top - 13))
                image = mark(image, spots)
                if crop_to and (box := rect(crop_to)):
                    left, top, right, bottom = box
                    image = image.crop((max(0, round((left - pad[0]) * dpr)), max(0, round((top - pad[1]) * dpr)),
                                        min(image.width, round((right + pad[2]) * dpr)), min(image.height, round((bottom + pad[3]) * dpr))))
                if image.width > 620:
                    image = image.resize((620, round(image.height * 620 / image.width)), Image.LANCZOS)
                image.save(OUT / f"qs-{name}.png", optimize=True)
                print("saved", name, flush=True)

            # The welcome.
            js("openWelcome()")
            shot("welcome-study", crop_to=".welcome-box")
            js("welcome.step = 3; renderWelcome()")
            time.sleep(1)
            # As a new user sees it: nothing set up yet (this run can see keys saved on the PC).
            js("document.querySelector('#welcome-ai-status').textContent = ''; document.querySelector('#welcome-key').placeholder = 'Paste your key'")
            shot("welcome-ai", crop_to=".welcome-box")
            js("closeWelcome(); setPref('studyWith', 'horu'); applyFields()")

            # A capture of the game.
            # The game's name in the toolbar (any window will do: nothing is captured from it),
            # and its screen read as auto-capture reads one.
            app._set_game_window(ctypes.windll.user32.GetDesktopWindow(), "MyGame.exe")
            app._requests.put_auto(horu_app.AutoRead(game_picture(), "MyGame.exe"))
            time.sleep(9)  # read, then translated
            js("document.querySelectorAll('.translation').forEach(r => r.dataset.reveal = 'true')")
            if not only or "hero" in only:
                # Horu's window down to just below the capture, beside the screen it read.
                time.sleep(0.8)
                bottom = rect(".capture")[3] + 18
                window = window_picture(hwnd)
                window = window.crop((0, 0, window.width, min(window.height, round(bottom * dpr))))
                hero(window, game_picture()).save(OUT / "hero.png", optimize=True)
                print("saved hero", flush=True)
            shot("read", [(1, "#toggle-auto"), (2, "#snip"), (3, "#toggle-furigana"), (4, "#toggle-screen"), (5, "#toggle-top"),
                          (6, "#clear"), (7, "#game-chip"), (8, "#hotkey-chip"), (9, "#open-settings")])
            # The line's own buttons, close up.
            # Its buttons show on hover: shown here, and named so they can be marked.
            buttons = js("(() => { const line = document.querySelector('.capture .line.dialogue') || document.querySelector('.capture .line');"
                         " return Array.from(line.querySelectorAll('.line-meta > .line-btn')).map((b, i) => { b.id = 'qs-btn-' + i; b.style.opacity = 1; return b.textContent; }); })()")
            print("line buttons:", buttons, flush=True)
            marks = [(n + 1, f"#qs-btn-{n}") for n in range(len(buttons or []))]
            shot("line", marks, where="above", crop_to=".capture", pad=(6, 6, 6, 6))

            # A word's popup.
            js("(() => { const words = Array.from(document.querySelectorAll('button.word')); const w = words.find(b => b.textContent.includes('駅')) || words[2]; w.click(); })()")
            time.sleep(1.5)
            shot("popup")
            js("(() => { const b = Array.from(document.querySelectorAll('#popover button')).find(b => b.textContent.trim() === 'Save'); if (b) b.click(); })()")
            time.sleep(1.5)
            js("closePopover()")

            # Explain.
            js("(() => { const b = Array.from(document.querySelectorAll('.capture .line-btn')).find(b => b.textContent === 'Explain'); if (b) b.click(); })()")
            time.sleep(14)
            shot("explain")
            js("document.querySelector('#tutor-close') && document.querySelector('#tutor-close').click()")

            # Words and Study.
            js("document.querySelector('#tab-words').click()")
            time.sleep(1.5)
            shot("words")
            js("document.querySelector('#tab-study').click()")
            time.sleep(1.5)
            js("document.querySelector('#start-review').click()")
            time.sleep(1.5)
            js("document.querySelector('#show-answer').click()")
            shot("review")
            js("document.querySelector('#end-review').click(); document.querySelector('#tab-read').click()")

            # Settings.
            js("document.querySelector('#open-settings').click(); showPane('capture')")
            shot("settings-capture")
            js("setPref('studyWith', 'anki'); applyFields(); showPane('study'); refreshAnkiSetups()")
            time.sleep(1.5)
            shot("settings-anki")
            js("setPref('studyWith', 'horu'); applyFields()")
        except Exception:
            import traceback

            traceback.print_exc()
        finally:
            time.sleep(1)
            if made:
                made[0].window.destroy()

    threading.Thread(target=drive, daemon=True).start()
    cli.main()
    print("scratch folder:", home)


if __name__ == "__main__":
    main()
