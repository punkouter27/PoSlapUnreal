"""Arena textures (Content/Art/Raw/Arena_v2.blend), generated: power-of-two, colour in sRGB, data linear.

    training/.venv/Scripts/python.exe Scripts/arena_textures.py

Per textured asset: <T>_D (base colour, sRGB), <T>_ORM (Unreal: R occlusion, G roughness, B metallic, linear),
<T>_MASK (Unity HDRP: R metallic, G occlusion, B detail mask, A smoothness = 1 - roughness, linear), optional <T>_N
(tangent-space normal, OpenGL +Y, from a height field on a flat UV layout = MikkTSpace-consistent).
UV layouts match the Blender UVs set in the arena session (podium regions by face axis, planar mat/deck, cylinder LED).
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parents[1] / "Content/Art/Exports/Textures"
OUT.mkdir(parents=True, exist_ok=True)
rng = np.random.default_rng(3)
FONT = "C:/Windows/Fonts/ariblk.ttf"


def noise(n, scale, octaves=4):
    """Value noise in [0, 1], n x n, tileable enough for dirt/smudges."""
    acc, amp, tot = np.zeros((n, n)), 1.0, 0.0
    for o in range(octaves):
        g = max(2, int(n / scale * 2 ** o))
        img = Image.fromarray((rng.random((g, g)) * 255).astype(np.uint8)).resize((n, n), Image.BICUBIC)
        acc += amp * np.asarray(img, float) / 255
        tot += amp
        amp *= 0.5
    return acc / tot


def save(name, d=None, rough=None, metal=None, ao=None, height=None, strength=2.0):
    if d is not None:
        Image.fromarray(np.clip(d * 255, 0, 255).astype(np.uint8)).save(OUT / f"{name}_D.png")
    if rough is not None:
        u8 = lambda a: np.clip(a * 255, 0, 255).astype(np.uint8)
        Image.fromarray(np.dstack([u8(ao), u8(rough), u8(metal)])).save(OUT / f"{name}_ORM.png")
        Image.fromarray(np.dstack([u8(metal), u8(ao), u8(np.ones_like(ao)), u8(1 - rough)]), "RGBA").save(OUT / f"{name}_MASK.png")
    if height is not None:
        gy, gx = np.gradient(height)
        nrm = np.dstack([-gx * strength, gy * strength, np.ones_like(height)])
        nrm /= np.linalg.norm(nrm, axis=2, keepdims=True)
        Image.fromarray(((nrm * 0.5 + 0.5) * 255).astype(np.uint8)).save(OUT / f"{name}_N.png")
    print("wrote", name)


def srgb(c):
    return np.array(c, float)


def text_mask(n_w, n_h, text, size, xy, rotate=0):
    """Text centred on xy, rotated about its own centre (drawn on its own canvas so nothing clips)."""
    f = ImageFont.truetype(FONT, size)
    l, t, r, b = f.getbbox(text)
    tile = Image.new("L", (r - l + 8, b - t + 8), 0)
    ImageDraw.Draw(tile).text((4 - l, 4 - t), text, fill=255, font=f)
    tile = tile.rotate(rotate, expand=True)
    im = Image.new("L", (n_w, n_h), 0)
    im.paste(tile, (int(xy[0] - tile.width / 2), int(xy[1] - tile.height / 2)))
    return np.asarray(im, float) / 255


# ---- podium (1024): u 0-0.25 = the +-Y faces (0.2 x 1.0 m, camera side: vertical logo), u 0.25-0.85 = the +-X faces
# (0.6 x 1.0 m, athletes' side: lacquer with hand smudges near the top), u 0.85-1 = top / bottom
N = 1024
d = np.ones((N, N, 3)) * srgb([0.035, 0.035, 0.04])
rough = 0.22 + 0.25 * (noise(N, 64) > 0.62) * noise(N, 16)          # smudges
v = np.linspace(1, 0, N)[:, None] * np.ones((1, N))                  # image row 0 = v 1 = podium top
u = np.ones((N, 1)) * np.linspace(0, 1, N)[None, :]
rough += 0.2 * (v > 0.75) * (u > 0.25) * (u < 0.85) * noise(N, 32)    # hands land near the top edge
metal = np.full((N, N), 0.3)
ao = 1 - 0.35 * np.clip((0.06 - v) / 0.06, 0, 1)                     # contact darkening at the floor
for lo, hi in ((0.0, 0.25), (0.25, 0.85), (0.85, 1.0)):              # worn edges: every region border is a face edge
    edge = np.minimum.reduce([np.abs(u - lo), np.abs(u - hi), np.abs(v - 0.0), np.abs(v - 1.0)])
    wear = (edge < 0.012) * (noise(N, 24) > 0.45) * ((u >= lo) & (u < hi))
    d += wear[..., None] * 0.12
    rough += wear * 0.25
logo = text_mask(N, N, "POSLAP", 120, (int(0.125 * N), N // 2), rotate=90)
gold = srgb([0.83, 0.62, 0.22])
d = d * (1 - logo[..., None]) + gold * logo[..., None]
metal = metal * (1 - logo) + logo
rough = rough * (1 - logo) + 0.3 * logo
save("T_Podium", d, np.clip(rough, 0, 1), metal, ao)

# ---- safety mat (2048, planar over x, y in [-2.42, 2.42] m): red vinyl, 12 seams, logo x4, referee's tape cross
N = 2048
xs = (np.arange(N) + 0.5) / N * 4.84 - 2.42
X, Y = np.meshgrid(xs, -xs)                                         # image row 0 = +y
R, A = np.hypot(X, Y), np.arctan2(Y, X)
seam =(np.abs(np.remainder(A + np.pi / 12, np.pi / 6) - np.pi / 12) * R < 0.006).astype(float)
seam = np.maximum(seam, (np.abs(R - 2.0) < 0.005).astype(float))
d = np.ones((N, N, 3)) * srgb([0.36, 0.03, 0.03]) * (0.9 + 0.2 * noise(N, 256))[..., None]
d *= (1 - 0.5 * seam)[..., None]
lg = np.zeros((N, N))
for k, ang in enumerate((45, 135, 225, 315)):
    a = np.radians(ang); r = 2.21
    cx, cy = (r * np.cos(a) + 2.42) / 4.84 * N, (2.42 - r * np.sin(a)) / 4.84 * N
    lg = np.maximum(lg, text_mask(N, N, "POSLAP", 70, (int(cx), int(cy)), rotate=ang - 90))
cross = (((np.abs(X) < 0.15) & (np.abs(Y + 2.0) < 0.025)) | ((np.abs(Y + 2.0) < 0.15) & (np.abs(X) < 0.025))).astype(float)
white = np.maximum(lg, cross)
d = d * (1 - white[..., None]) + 0.85 * white[..., None]
scuff = (noise(N, 128) > 0.6) * noise(N, 24)
rough = np.clip(0.78 - 0.25 * scuff + 0.1 * white, 0, 1)
ao = 1 - 0.5 * np.asarray(Image.fromarray((seam * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(3)), float) / 255
height = -np.asarray(Image.fromarray((seam * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(2)), float) / 255
save("T_SafetyMat", d, rough, np.zeros((N, N)), ao, height, strength=4.0)

# ---- stage deck (1024, planar over [-3.6, 3.6] m): dark gloss with scuffs and footprints of traffic toward the mat
N = 1024
d = np.ones((N, N, 3)) * srgb([0.03, 0.03, 0.04]) * (0.95 + 0.08 * noise(N, 128))[..., None]
scuff = noise(N, 8) * (noise(N, 96) > 0.5)                          # fine scuffs, not mirror patches
rough = np.clip(0.62 + 0.15 * scuff, 0, 1)                           # satin: gloss mirrored the crowd as blotches
save("T_StageDeck", d, rough, np.zeros((N, N)), np.ones((N, N)))

# ---- floor tape (512 x 64, along the strip): yellow gaffer tape, worn and dirty
W, H = 512, 64
tape = np.ones((H, W, 3)) * srgb([0.86, 0.68, 0.12])
wear = noise(512, 32)[:H, :W]
tape = tape * (0.8 + 0.2 * wear[..., None]) + (wear < 0.22)[..., None] * (srgb([0.12, 0.1, 0.06]) - tape) * 0.5
Image.fromarray(np.clip(tape * 255, 0, 255).astype(np.uint8)).save(OUT / "T_Tape_D.png")
rt = np.clip(0.55 + 0.3 * (wear < 0.22), 0, 1)
u8 = lambda a: np.clip(a * 255, 0, 255).astype(np.uint8)
Image.fromarray(np.dstack([u8(np.ones((H, W))), u8(rt), u8(np.zeros((H, W)))])).save(OUT / "T_Tape_ORM.png")
Image.fromarray(np.dstack([u8(np.zeros((H, W))), u8(np.ones((H, W))), u8(np.ones((H, W))), u8(1 - rt)]), "RGBA").save(OUT / "T_Tape_MASK.png")
print("wrote T_Tape")

# ---- LED ring (2048 x 256, emissive; the ring repeats it 4x around): event graphics, read from inside the bowl
W, H = 2048, 256
im = Image.new("RGB", (W, H), (4, 4, 8))
dr = ImageDraw.Draw(im)
for x in range(0, W, 8):                                             # LED pixel grid
    dr.line([(x, 0), (x, H)], fill=(0, 0, 0))
f = ImageFont.truetype(FONT, 110)
dr.rectangle([0, 0, 520, H], fill=(150, 12, 10))
dr.text((260, H // 2), "RED", font=f, fill=(255, 235, 230), anchor="mm")
dr.text((W // 2, H // 2), "ROUND 1", font=f, fill=(255, 210, 90), anchor="mm")
dr.rectangle([W - 520, 0, W, H], fill=(14, 40, 170))
dr.text((W - 260, H // 2), "BLUE", font=f, fill=(230, 238, 255), anchor="mm")
im.save(OUT / "T_LED_E.png")
print("wrote T_LED_E")
