"""Annotate before and after screenshots for the Match Presentation enhancements (261030).

Run from repo root: python SCREENSHOTS/261030_match_presentation/annotate.py
"""

import os
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
BAR = 58
AMBER = (255, 176, 32)
RED = (255, 92, 92)
GREEN = (96, 220, 120)
CYAN = (64, 200, 255)
GOLD = (255, 215, 0)

try:
    FONT = ImageFont.truetype("arialbd.ttf", 19)
    FONT_SM = ImageFont.truetype("arial.ttf", 15)
except OSError:
    FONT = FONT_SM = ImageFont.load_default()


def annotate(name, title, marks):
    """marks: list of (box, colour, number, note). box is (x0, y0, x1, y1) in image pixels."""
    im = Image.open(os.path.join(HERE, name)).convert("RGB")
    out = Image.new("RGB", (im.width, im.height + BAR), (18, 18, 22))
    out.paste(im, (0, BAR))
    d = ImageDraw.Draw(out, "RGBA")
    d.text((10, 6), title, font=FONT, fill=(245, 245, 248))
    d.text((10, 32), "   ".join(f"({n}) {t}" for _b, _c, n, t in marks)[:120], font=FONT_SM, fill=(168, 172, 182))
    for (x0, y0, x1, y1), colour, num, _t in marks:
        d.rectangle((x0, y0 + BAR, x1, y1 + BAR), outline=colour, width=3)
        d.rectangle((x0, y0 + BAR, x0 + 26, y0 + BAR + 26), fill=colour)
        d.text((x0 + 7, y0 + BAR + 4), str(num), font=FONT_SM, fill=(16, 16, 20))
    dst = os.path.join(HERE, name.replace(".png", "_annotated.png"))
    out.save(dst)
    print("wrote", os.path.relpath(dst, os.path.dirname(HERE)))


annotate("before.png", "BEFORE - Match HUD (261026)", [
    ((8, 28, 532, 60), AMBER, 1, "Generic match line"),
    ((6, 75, 534, 98), AMBER, 2, "Basic momentum bar"),
    ((8, 105, 532, 160), RED, 3, "No round pips or bout rules"),
    ((498, 620, 530, 656), CYAN, 4, "Isolated palm gauge"),
])

annotate("after.png", "AFTER - Match Presentation Suite (261030)", [
    ((8, 26, 532, 54), GREEN, 1, "Bout Stakes & Rules intro"),
    ((8, 55, 532, 78), GOLD, 2, "6-Turn Pip Meter [R1-R3]"),
    ((6, 80, 534, 102), GREEN, 3, "Persistent Momentum Tug-of-War"),
    ((150, 400, 390, 520), CYAN, 4, "Impact Telemetry & Ruling"),
])
