"""Draw the callouts on the before/after pairs of the UI compaction (261026).

Each pair gets a caption bar on top and numbered highlight boxes on the image.
Run from the repo root:  python SCREENSHOTS/261026_ui_compaction/annotate.py
"""

import os
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
BAR = 58
AMBER = (255, 176, 32)
CYAN = (64, 200, 255)
RED = (255, 92, 92)
GREEN = (96, 220, 120)

try:
    FONT = ImageFont.truetype("arialbd.ttf", 19)
    FONT_SM = ImageFont.truetype("arial.ttf", 15)
except OSError:
    FONT = ImageFont.load_default()
    FONT_SM = FONT


def annotate(name, title, notes, marks):
    """marks: list of (box, colour, number). box is (x0, y0, x1, y1) in image pixels."""
    src = os.path.join(HERE, name)
    im = Image.open(src).convert("RGB")
    out = Image.new("RGB", (im.width, im.height + BAR), (18, 18, 22))
    out.paste(im, (0, BAR))
    d = ImageDraw.Draw(out, "RGBA")

    d.text((10, 6), title, font=FONT, fill=(245, 245, 248))
    legend = "   ".join(f"({n}) {t}" for n, _c, t in notes)
    d.text((10, 32), legend[:210], font=FONT_SM, fill=(168, 172, 182))

    for (x0, y0, x1, y1), colour, num in marks:
        box = (x0, y0 + BAR, x1, y1 + BAR)
        d.rectangle(box, outline=colour, width=3)
        d.rectangle((x0, y0 + BAR, x0 + 26, y0 + BAR + 26), fill=colour)
        d.text((x0 + 7, y0 + BAR + 4), str(num), font=FONT_SM, fill=(16, 16, 20))

    dst = os.path.join(HERE, name.replace(".png", "_annotated.png"))
    out.save(dst)
    print("wrote", os.path.relpath(dst, os.path.dirname(HERE)))


annotate(
    "before_hud.png",
    "BEFORE - HUD canvas panel (top left)",
    [(1, RED, "fixed 300x250 black box"), (2, AMBER, "9 lines of telemetry")],
    [((8, 28, 312, 288), RED, 1)],
)

annotate(
    "after_hud.png",
    "AFTER - HUD canvas panel (top left)",
    [(1, GREEN, "fits the text: 6 lines, translucent")],
    [((8, 28, 345, 152), GREEN, 1)],
)

annotate(
    "before_menu0.png",
    "BEFORE - MATCH sheet",
    [
        (1, RED, "judges table: 4 rows"),
        (2, RED, "badges: 2 rows of 4+3"),
        (3, RED, "build stamp hidden behind the sheet"),
    ],
    [
        ((8, 172, 308, 262), RED, 1),
        ((8, 876, 532, 930), RED, 2),
        ((468, 938, 540, 960), RED, 3),
    ],
)

annotate(
    "after_menu0.png",
    "AFTER - MATCH sheet",
    [
        (1, GREEN, "judges: one line"),
        (2, GREEN, "badges: one row of 7"),
        (3, GREEN, "build stamp visible"),
    ],
    [
        ((8, 120, 340, 140), GREEN, 1),
        ((8, 898, 532, 922), GREEN, 2),
        ((470, 938, 540, 960), GREEN, 3),
    ],
)

annotate(
    "before_menu1.png",
    "BEFORE - ROSTER sheet",
    [(1, RED, "4x4 grid plus wrapped card text")],
    [((8, 850, 532, 962), RED, 1)],
)

annotate(
    "after_menu1.png",
    "AFTER - ROSTER sheet",
    [(1, GREEN, "4x4 grid, card text cut to 4 lines")],
    [((8, 850, 532, 962), GREEN, 1)],
)

annotate(
    "before_menu2.png",
    "BEFORE - ABOUT sheet",
    [(1, RED, "long lines wrap; 8 rows of legend")],
    [((8, 820, 532, 962), RED, 1)],
)

annotate(
    "after_menu2.png",
    "AFTER - ABOUT sheet",
    [(1, GREEN, "two columns, no wrap, 6 rows")],
    [((8, 800, 532, 962), GREEN, 1)],
)
