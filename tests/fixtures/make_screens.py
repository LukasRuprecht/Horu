"""Draws the made-up game screens the auto-capture tests watch (tests/fixtures/screen_*.png).

    uv run python tests/fixtures/make_screens.py

The pictures are committed, so the tests don't need this script or a font: run it only to
change them. They are drawn from scratch (shapes for scenery, a dialogue box, text in a
Japanese system font) and show no real game. Each is the size the watcher shrinks a game
picture to, with text as small as a game's is at that size.

  line            a name tag and a line in a dialogue box, over a street
  choices         the same, with two choices up and to the right
  street          the street alone
  thought         the street with a thought bubble
  sign            a shop sign in the street
  box_0..3        a line in a see-through box while something moves behind it
  talk_before     a cafe, nobody talking
  talk_a_1, _2    a line under a tilted name tag, the scene behind the tag moving
  talk_b_1, _2    the next line, three characters long
"""

from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).parent
SIZE = (480, 270)
FONTS = ["C:/Windows/Fonts/YuGothB.ttc", "C:/Windows/Fonts/meiryob.ttc", "C:/Windows/Fonts/msgothic.ttc"]


def font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONTS:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    raise SystemExit("No Japanese font found: add one to FONTS.")


def street(seed: int = 1, sway: int = 0) -> Image.Image:
    """Soft shapes: buildings, a road, lamps. Blurred, so none of it looks like writing."""
    rng = random.Random(seed)
    image = Image.new("RGB", SIZE, (70, 96, 140))
    draw = ImageDraw.Draw(image)
    for x in range(0, SIZE[0], 60):
        height = rng.randint(70, 150)
        shade = rng.randint(60, 110)
        draw.rectangle((x + sway, 190 - height, x + 52 + sway, 190), fill=(shade, shade - 10, shade + 15))
    draw.rectangle((0, 190, SIZE[0], SIZE[1]), fill=(58, 60, 66))
    for x in range(30, SIZE[0], 110):
        draw.ellipse((x - sway, 120, x + 14 - sway, 134), fill=(190, 170, 120))
    return image.filter(ImageFilter.GaussianBlur(3))


def box(image: Image.Image, where: tuple[int, int, int, int], fill=(16, 18, 30), see_through: float = 0.0) -> None:
    patch = Image.new("RGB", (where[2] - where[0], where[3] - where[1]), fill)
    if see_through:
        patch = Image.blend(patch, image.crop(where), see_through)
    image.paste(patch, where[:2])


def write(image: Image.Image, at: tuple[int, int], text: str, size: int = 14, fill=(245, 245, 245), tilt: float = 0.0) -> None:
    if not tilt:
        ImageDraw.Draw(image).text(at, text, font=font(size), fill=fill)
        return
    f = font(size)
    width, height = int(f.getlength(text)) + 8, size + 10
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((4, 2), text, font=f, fill=fill)
    layer = layer.rotate(tilt, expand=True, resample=Image.BICUBIC)
    image.paste(layer, at, layer)


def talking(scene: Image.Image, name: str | None, text: str | None, tilt: float = 0.0, tag_see_through: float = 0.0) -> Image.Image:
    image = scene.copy()
    box(image, (40, 212, 400, 258))
    if name:
        wide = 40 if tag_see_through else 0  # a see-through tag reaches past its name
        box(image, (46, 190, 46 + 16 * len(name) + 14 + wide, 212), fill=(120, 20, 30), see_through=tag_see_through)
        write(image, (52, 192), name, tilt=tilt)
    if text:
        write(image, (56, 224), text, size=15)
    return image


def main() -> None:
    out = {}
    scene = street()
    out["street"] = scene
    out["line"] = talking(scene, "ミナト", "この道をまっすぐ行けば駅に着くよ。")
    choices = out["line"].copy()
    for row, text in enumerate(("ありがとう", "もう一度お願い")):
        box(choices, (270, 118 + 34 * row, 356, 142 + 34 * row), fill=(235, 235, 235))
        write(choices, (276, 122 + 34 * row), text, size=12, fill=(20, 20, 20))
    out["choices"] = choices
    thought = scene.copy()
    ImageDraw.Draw(thought).ellipse((250, 40, 440, 100), fill=(240, 240, 250))
    write(thought, (272, 60), "どこかで見た顔だな…", size=14, fill=(30, 30, 40))
    out["thought"] = thought
    sign = scene.copy()
    box(sign, (150, 60, 300, 92), fill=(230, 220, 190))
    write(sign, (160, 66), "喫茶　やまびこ", size=16, fill=(60, 30, 20))
    out["sign"] = sign

    # Something flapping behind a see-through box: the text stays put.
    for k in range(4):
        behind = street(seed=3)
        draw = ImageDraw.Draw(behind)
        lift = (0, 18, 34, 12)[k]
        draw.polygon([(120, 230), (40, 200 - lift), (110, 214)], fill=(150, 120, 90))
        draw.polygon([(240, 230), (330, 200 - lift), (250, 214)], fill=(150, 120, 90))
        draw.ellipse((120, 196, 240, 262), fill=(130, 100, 70))
        behind = behind.filter(ImageFilter.GaussianBlur(2))
        box(behind, (30, 180, 450, 260), fill=(10, 10, 20), see_through=0.35)
        write(behind, (50, 192), "森を抜けると　大きな湖が", size=16)
        write(behind, (50, 222), "見えてくるはずじゃ。", size=16)
        out[f"box_{k}"] = behind

    # A cafe: the tilted name tag is see-through, and the scene behind it never stops moving.
    out["talk_before"] = talking(street(seed=5), None, None)
    for n, sway in ((1, 0), (2, 5)):
        cafe = street(seed=5, sway=sway)
        # Sharp, bright things passing behind the tag (cups on a shelf, say): its strokes change.
        shelf = ImageDraw.Draw(cafe)
        for x in range(118, 160, 12):
            shelf.rectangle((x + sway, 188, x + 4 + sway, 214), fill=(255, 255, 255))
        out[f"talk_a_{n}"] = talking(cafe, "マスター", "いつもの席、空いてるよ。", tilt=7, tag_see_through=0.8)
        out[f"talk_b_{n}"] = talking(cafe, "マスター", "どうぞ。", tilt=7, tag_see_through=0.8)
    for name, image in out.items():
        image.save(HERE / f"screen_{name}.png", optimize=True)
    print("wrote", len(out), "pictures to", HERE)


if __name__ == "__main__":
    main()
