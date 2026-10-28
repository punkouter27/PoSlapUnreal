"""Bundle the web version for static hosting: python Web/build.py  ->  Web/dist/ (same layout as the dev server:
dist/Web/ holds the page and its libraries, dist/training/ the arena, fingerprints, cards and the two brains loader.js names).
"""
import re
import shutil
import time
from pathlib import Path

WEB = Path(__file__).resolve().parent
ROOT, DIST = WEB.parent, WEB / "dist"
LIBS = ["@mujoco/mujoco/mujoco.js", "@mujoco/mujoco/mujoco.wasm",
        "onnxruntime-web/dist/ort.min.mjs", "onnxruntime-web/dist/ort-wasm-simd-threaded.jsep.mjs",
        "onnxruntime-web/dist/ort-wasm-simd-threaded.jsep.wasm",
        "three/build/three.module.js", "three/examples/jsm/controls/OrbitControls.js",
        "three/examples/jsm/loaders/GLTFLoader.js", "three/examples/jsm/utils/BufferGeometryUtils.js",
        "three/examples/jsm/libs/meshopt_decoder.module.js"]

loader = (WEB / "loader.js").read_text(encoding="utf-8")
chars = sorted(set(re.findall(r'char: "(\w+)"', loader)))
brains = re.findall(r'brain: "(\w+)"', loader)
xmls = re.findall(r'xml: "([\w.]+)"', loader)
data = [f"training/models/{x}" for x in xmls] + [f"training/models/{c}_fingerprint.json" for c in chars] \
    + [f"training/characters/{c}.json" for c in chars] + [f"training/models/brains/{b}.onnx" for b in brains] \
    + [f for f in ["training/models/league.json"] if (ROOT / f).exists()]   # scouting (app.js reads it if present)

shutil.rmtree(DIST, ignore_errors=True)
for src, dst in [(WEB / f, DIST / "Web" / f) for f in ("index.html", "app.js", "sim.js", "loader.js", "art.js", "assets/arena.glb")] \
        + [(WEB / "assets" / f"{c}.glb", DIST / "Web" / "assets" / f"{c}.glb") for c in chars] \
        + [(WEB / "node_modules" / f, DIST / "Web" / "node_modules" / f) for f in LIBS] \
        + [(ROOT / f, DIST / f) for f in data]:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
page = DIST / "Web" / "index.html"   # the build stamp (bottom right): the build date
page.write_text(re.sub(r'<div class="stamp">[^<]*</div>', f'<div class="stamp">{time.strftime("%y%m%d")}</div>', page.read_text(encoding="utf-8")), encoding="utf-8")
(DIST / "index.html").write_text('<!doctype html><meta charset="utf-8"><title>PoSlap Arena</title>'
                                 '<meta http-equiv="refresh" content="0; url=Web/"><a href="Web/">PoSlap Arena</a>', encoding="utf-8")
(DIST / "staticwebapp.config.json").write_text(
    '{ "mimeTypes": { ".wasm": "application/wasm", ".mjs": "text/javascript", ".onnx": "application/octet-stream", ".xml": "text/xml", ".glb": "model/gltf-binary" } }\n')


def _fail_lfs_pointers():
    """Ship-blocker: an unpulled git-LFS pointer looks like a tiny valid file but breaks browsers."""
    bad = []
    for p in DIST.rglob("*"):
        if p.is_file() and p.open("rb").read(64).startswith(b"version https://git-lfs"):
            bad.append(str(p.relative_to(DIST)))
    if bad:
        raise SystemExit("LFS pointers in dist (run 'git lfs pull'): " + ", ".join(bad))



_fail_lfs_pointers()

size = sum(p.stat().st_size for p in DIST.rglob("*") if p.is_file())
print(f"Web/dist: {sum(1 for p in DIST.rglob('*') if p.is_file())} files, {size / 1e6:.1f} MB (brains: {', '.join(brains)})")
