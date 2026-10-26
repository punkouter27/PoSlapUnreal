"""Draw the callouts on the before/after shots of the ten HUD options (261026).

Run from the repo root:  python SCREENSHOTS/261026_hud_ten/annotate.py
"""

import os
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
BAR = 58
AMBER = (255, 176, 32)
RED = (255, 92, 92)
GREEN = (96, 220, 120)
CYAN = (64, 200, 255)

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


annotate("before_hud.png", "BEFORE - HUD", [
    ((8, 28, 365, 162), AMBER, 8, "6-line panel"),
    ((8, 166, 534, 280), RED, 1, "dead band"),
    ((490, 375, 535, 400), AMBER, 7, "PALM word"),
    ((8, 832, 526, 940), RED, 6, "tall panels hide feet"),
])
annotate("after_hud.png", "AFTER - HUD", [
    ((8, 28, 200, 72), GREEN, 8, "3 short lines"),
    ((6, 80, 534, 100), GREEN, 1, "bar under panel"),
    ((498, 620, 530, 656), GREEN, 7, "number only"),
    ((8, 885, 526, 940), GREEN, 6, "strips"),
    ((246, 938, 294, 957), CYAN, 2, "grip"),
])
annotate("before_menu0.png", "BEFORE - MATCH tab", [
    ((300, 857, 444, 882), AMBER, 5, "replay, fixed 1/2"),
])
annotate("after_menu0.png", "AFTER - MATCH tab", [
    ((300, 857, 528, 882), GREEN, 5, "replay + x1/4 pace"),
    ((246, 938, 294, 957), CYAN, 2, "grip"),
])
annotate("after_menu2.png", "AFTER - ABOUT tab", [
    ((6, 738, 400, 764), GREEN, 10, "swipe / double tap"),
])
annotate("after_result.png", "AFTER - match over", [
    ((163, 786, 377, 880), GREEN, 4, "result card + REMATCH"),
    ((332, 128, 532, 246), CYAN, 1, "cards under the bar"),
    ((205, 34, 280, 50), GREEN, 3, "hit label"),
])
annotate("after_notch.png", "AFTER - fake notch 40 px + nav bar 28 px", [
    ((0, 0, 540, 40), RED, 9, "notch inset"),
    ((0, 932, 540, 960), RED, 9, "nav bar inset"),
    ((4, 44, 536, 74), GREEN, 9, "anchors inside"),
])
