"""Scuff and wear decals for the arena (podium and floor), generated: RGBA PNG, colour in sRGB, alpha = opacity.

    training/.venv/Scripts/python.exe Scripts/arena_decals.py

Writes Content/Art/Exports/Decals/ (512 x 512 unless noted, power of two, straight alpha, transparent edges so a
decal never shows its box):
  T_Decal_SoleSkid_01 / _02   dark rubber streak from a skidding sole: runs along +U (texture X), heel end at U 0.
                              For the safety mat and stage deck. Size ~30 x 8 cm (02: 40 x 10 cm, a longer slide).
  T_Decal_PalmSmudge_01       greasy palm print, lighter + glossier than the black lacquer: the defender's braced palms
                              (BLUEPRINT 9) on the podium top. Fingers toward +V (texture up). ~20 x 20 cm.
  T_Decal_PodiumChip_01       chipped lacquer showing the pale undercoat, 1024 x 256, along an edge: the edge is the
                              texture's top row (V 1). Podium top edges, the striker's side. ~60 x 15 cm.
  T_Decal_FloorScratch_01     a patch of fine pale scratches and dust for the dark stage deck around the foot boxes. ~50 cm.
Use (Unreal): a deferred decal material (Translucent blend, DBuffer Color + Roughness): BaseColor = RGB, Opacity = A,
Roughness = the per-decal value in DECALS below (the smudge is glossier than the lacquer, rubber is rougher). Import with
sRGB on and the default (BC7/DXT5) compression; the decals are mobile-safe (<= 1024, DBuffer decals need
r.DBuffer=1, which the Android forward renderer does not have: on a phone use them as mesh decals or skip).
A preview sheet (on the deck / lacquer colours) goes to Content/Art/Exports/Decals/Decals_preview.png.
"""
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

OUT = Path(__file__).resolve().parents[1] / "Content/Art/Exports/Decals"
OUT.mkdir(parents=True, exist_ok=True)
rng = np.random.default_rng(11)
DECALS = {}


def noise(h, w, scale, octaves=4):
    acc, amp, tot = np.zeros((h, w)), 1.0, 0.0
    for o in range(octaves):
        gh, gw = max(2, int(h / scale * 2 ** o)), max(2, int(w / scale * 2 ** o))
        img = Image.fromarray((rng.random((gh, gw)) * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC)
        acc += amp * np.asarray(img, float) / 255
        tot += amp
        amp *= 0.5
    return acc / tot


def blur(a, r):
    return np.asarray(Image.fromarray(np.clip(a * 255, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(r)), float) / 255


def edge_fade(h, w, margin=0.06, top=True):
    """1 inside, 0 at the border: no decal ever shows its box (top=False: the top row is a real edge, kept)."""
    y, x = np.mgrid[0:h, 0:w]
    m = np.minimum.reduce([x / (w * margin), (w - 1 - x) / (w * margin), y / (h * margin) if top else np.ones((h, w)) * 9,
                           (h - 1 - y) / (h * margin)])
    return np.clip(m, 0, 1)


def save(name, rgb, alpha, roughness, use, size_cm, top=True):
    alpha = np.clip(alpha * edge_fade(*alpha.shape, top=top), 0, 1)
    rgba = np.dstack([np.clip(rgb, 0, 1), alpha])
    Image.fromarray((rgba * 255).round().astype(np.uint8), "RGBA").save(OUT / f"{name}.png", optimize=True)
    DECALS[name] = {"size_px": list(alpha.shape[::-1]), "roughness": roughness, "use": use, "size_cm": size_cm}
    print("wrote", name)


def skid(name, length, width, seed_bend, size_cm):
    N = 512
    y, x = np.mgrid[0:N, 0:N] / N
    centre = 0.5 + seed_bend * (x - 0.5) ** 2                     # a slight arc, as a foot pivots while it slides
    t = np.clip((x - (0.5 - length / 2)) / length, 0, 1)            # 0 at the heel end, 1 at the toe end
    ends = np.clip((length / 2 - np.abs(x - 0.5)) / (0.12 * length), 0, 1)
    inside = ends * ends * (3 - 2 * ends)                             # soft ends, no cut
    half = width / 2 * (0.55 + 0.45 * np.sin(np.pi * t) ** 0.5)     # thin at both ends
    d = np.abs(y - centre) / np.maximum(half, 1e-3)
    body = np.clip(1 - d, 0, 1) ** 0.6 * inside * (0.35 + 0.65 * t ** 0.4)   # heavier where the sole bit in
    stri = noise(N, N, 3, 2)
    stri = np.asarray(Image.fromarray((stri * 255).astype(np.uint8)).resize((N // 16, N)).resize((N, N)), float) / 255
    a = body * (0.55 + 0.6 * stri) * (0.7 + 0.3 * noise(N, N, 40))
    a = blur(np.clip(a, 0, 1), 1.2) * 0.85
    rgb = np.ones((N, N, 3)) * np.array([0.045, 0.04, 0.038])       # black rubber, a touch warm
    save(name, rgb, a, 0.85, "safety mat / stage deck: a sole skid (ASlapHUD's sole marks)", size_cm)


skid("T_Decal_SoleSkid_01", 0.78, 0.2, 0.25, [30, 8])
skid("T_Decal_SoleSkid_02", 0.86, 0.24, -0.35, [40, 10])

# palm smudge: the palm heel, the four fingers toward +V, the thumb to one side; a lighter, glossier film
N = 512
y, x = np.mgrid[0:N, 0:N] / N
v = 1 - y
palm = np.clip(1 - np.hypot((x - 0.5) / 0.2, (v - 0.36) / 0.17), 0, 1)
fingers = np.zeros_like(x)
for fx, top, w in ((0.36, 0.83, 0.045), (0.46, 0.9, 0.048), (0.56, 0.88, 0.047), (0.65, 0.78, 0.042)):
    along = np.clip((v - 0.5) / (top - 0.5), 0, 1)
    fingers = np.maximum(fingers, np.clip(1 - np.abs(x - fx - 0.02 * along) / w, 0, 1) * (v > 0.48) * (v < top)
                         * np.clip((top - v) / 0.04, 0, 1))
thumb_axis = np.array([-0.7, 0.45]) / np.hypot(-0.7, 0.45)
px, pv = x - 0.33, v - 0.42
along = px * thumb_axis[0] + pv * thumb_axis[1]
across = np.abs(-px * thumb_axis[1] + pv * thumb_axis[0])
thumb = np.clip(1 - across / 0.05, 0, 1) * (along > 0) * (along < 0.2)
shape = np.maximum.reduce([palm ** 0.5, fingers, thumb])
grain = 0.55 + 0.45 * noise(N, N, 6, 3)                              # skin ridges and uneven grease
a = blur(shape, 3) * grain * 0.42
rgb = np.ones((N, N, 3)) * np.array([0.36, 0.35, 0.34])
save("T_Decal_PalmSmudge_01", rgb, a, 0.12, "podium top: the defender's braced palm (BLUEPRINT 9)", [20, 20])

# podium edge chips: the edge is the top row; chips bite down from it, the pale undercoat shows
W, H = 1024, 256
y, x = np.mgrid[0:H, 0:W]
depth = (noise(1, W, 30, 3)[0] ** 3) * 0.4 * H                  # how far each chip reaches down from the edge
n2 = noise(H, W, 12, 3)
chip = ((y < depth[None, :] * (0.6 + 0.6 * n2)) & (noise(H, W, 60, 2) > 0.5)).astype(float)
chip = blur(chip, 0.8)
rim = blur(chip, 3) - chip                                          # a dark broken-lacquer lip round each chip
rgb = np.ones((H, W, 3)) * np.array([0.72, 0.66, 0.55]) * (0.85 + 0.15 * n2[..., None])
rgb = rgb * (1 - np.clip(rim * 2, 0, 0.5)[..., None])
a = np.clip(chip + rim * 0.6, 0, 1)
save("T_Decal_PodiumChip_01", rgb, a, 0.7, "podium top edges, the striker's side: chipped lacquer", [60, 15], top=False)

# floor scratches: thin pale lines in two main directions + dust
N = 1024
img = Image.new("L", (N, N), 0)
from PIL import ImageDraw  # noqa: E402

dr = ImageDraw.Draw(img)
for k in range(160):
    ang = rng.normal(0.25 if k % 3 else -1.1, 0.25)
    L = rng.uniform(0.05, 0.3) * N
    cx, cy = rng.normal(0.5, 0.17, 2) * N
    dx, dy = np.cos(ang) * L / 2, np.sin(ang) * L / 2
    dr.line([(cx - dx, cy - dy), (cx + dx, cy + dy)], fill=int(rng.uniform(60, 200)), width=int(rng.integers(1, 3)))
lines = np.asarray(img, float) / 255
y, x = np.mgrid[0:N, 0:N] / N
spot = np.clip(1 - np.hypot(x - 0.5, y - 0.5) / 0.45, 0, 1) ** 0.7
dust = (noise(N, N, 50) > 0.55) * noise(N, N, 6) * 0.35
a = np.clip((blur(lines, 0.6) * 0.8 + dust) * spot, 0, 1)
rgb = np.ones((N, N, 3)) * np.array([0.55, 0.54, 0.52])
save("T_Decal_FloorScratch_01", rgb, a, 0.75, "stage deck round the foot boxes: traffic scratches and dust", [50, 50])

# preview: each decal over the surface it is meant for
bgs = {"T_Decal_SoleSkid_01": (92, 8, 8), "T_Decal_SoleSkid_02": (92, 8, 8), "T_Decal_PalmSmudge_01": (9, 9, 10),
       "T_Decal_PodiumChip_01": (9, 9, 10), "T_Decal_FloorScratch_01": (8, 8, 10)}
tiles = []
for name in DECALS:
    im = Image.open(OUT / f"{name}.png")
    im.thumbnail((384, 384))
    bg = Image.new("RGBA", (384, 384), bgs[name] + (255,))
    bg.alpha_composite(im, ((384 - im.width) // 2, (384 - im.height) // 2))
    tiles.append(bg)
sheet = Image.new("RGBA", (384 * len(tiles), 384))
for i, t in enumerate(tiles):
    sheet.paste(t, (i * 384, 0))
sheet.convert("RGB").save(OUT / "Decals_preview.png")
(OUT / "decals.json").write_text(json.dumps(DECALS, indent=1))
print("wrote preview + decals.json")
