#!/usr/bin/env python3
"""Regenerate static/og-image.png in the ledger design system (< 150 KB)."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT = REPO_ROOT / "static" / "og-image.png"

INK_DEEP = (14, 20, 32)
INK = (18, 24, 42)
CREAM = (250, 248, 244)
CORAL = (255, 107, 74)
MUTED = (250, 248, 244, 170)


def _font(candidates, size):
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


SERIF = [
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
]
SANS = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]


def main():
    img = Image.new("RGB", (1200, 630), INK_DEEP)
    draw = ImageDraw.Draw(img, "RGBA")

    # soft coral glow, top-right
    glow = Image.new("L", (1200, 630), 0)
    gd = ImageDraw.Draw(glow)
    gd.ellipse((760, -260, 1420, 400), fill=70)
    img.paste(Image.new("RGB", img.size, CORAL), (0, 0), glow.point(lambda v: v // 2))

    # logo mark (scaled x3): ink square with coral + cream pixels
    x0, y0, s = 96, 96, 90
    draw.rounded_rectangle((x0, y0, x0 + s, y0 + s), radius=24, fill=INK,
                           outline=(250, 248, 244, 40), width=2)
    draw.rounded_rectangle((x0 + 18, y0 + 18, x0 + 42, y0 + 42), radius=6, fill=CORAL)
    draw.rounded_rectangle((x0 + 48, y0 + 48, x0 + 72, y0 + 72), radius=6, fill=CREAM)

    draw.text((x0 + s + 28, y0 + 22), "Statement Converter", font=_font(SERIF, 46), fill=CREAM)

    draw.text((96, 300), "Every statement, rebuilt as a", font=_font(SERIF, 64), fill=CREAM)
    draw.text((96, 378), "workbook you can trust.", font=_font(SERIF, 64), fill=CORAL)

    draw.text((96, 500), "Bank statement PDFs → exact-copy Excel · typed or scanned",
              font=_font(SANS, 30), fill=MUTED)

    img.save(OUT, optimize=True)
    size_kb = OUT.stat().st_size / 1024
    print(f"wrote {OUT} ({size_kb:.0f} KB)")
    assert size_kb < 150, "og-image too large"


if __name__ == "__main__":
    main()
