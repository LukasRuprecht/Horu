"""Horu's icon: ほ on a rounded square, as in the window's top bar (the Horu palette's accent).

    uv run python -m horu.icon     # writes resources/horu.ico and resources/horu.png

The tray and the window title bar use resources/horu.ico; the installer will too.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

RESOURCES = Path(__file__).parent / "resources"
ICO_PATH = RESOURCES / "horu.ico"
PNG_PATH = RESOURCES / "horu.png"
_FONT = Path(__file__).parent / "ui" / "fonts" / "NotoSansJP.woff2"
_FILL, _INK = (242, 193, 78), (27, 30, 38)  # --accent and --accent-ink of the Horu palette
_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def draw(size: int) -> Image.Image:
    """The icon at `size` pixels square (drawn larger, then shrunk, for smooth edges)."""
    scale = 4
    big = size * scale
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    canvas = ImageDraw.Draw(image)
    canvas.rounded_rectangle((0, 0, big - 1, big - 1), radius=round(big * 7 / 24), fill=_FILL)
    font = ImageFont.truetype(str(_FONT), round(big * 0.66))
    font.set_variation_by_name("Bold")
    left, top, right, bottom = canvas.textbbox((0, 0), "ほ", font=font)
    canvas.text(((big - (right - left)) / 2 - left, (big - (bottom - top)) / 2 - top), "ほ", font=font, fill=_INK)
    return image.resize((size, size), Image.LANCZOS)


def write() -> None:
    RESOURCES.mkdir(exist_ok=True)
    draw(256).save(ICO_PATH, sizes=[(s, s) for s in _SIZES])
    draw(512).save(PNG_PATH)


if __name__ == "__main__":
    write()
    print(f"Wrote {ICO_PATH} and {PNG_PATH}")
