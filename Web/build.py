"""Bundle the web version for static hosting: python Web/build.py  ->  Web/dist/ (same layout as the dev server:
dist/Web/ holds the page and its libraries, dist/training/ the arenas, fingerprints, cards and the brains loader.js names).

The one version stamp (UTC yyMMdd of the HEAD commit) is written into index.html and diag.html here; /diag's check list
(Web/diag_checks.json) is validated against what is bundled.
"""

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

WEB = Path(__file__).resolve().parent
ROOT, DIST = WEB.parent, WEB / "dist"
LIBS = [
    "@mujoco/mujoco/mujoco.js",
    "@mujoco/mujoco/mujoco.wasm",
    "onnxruntime-web/dist/ort.min.mjs",
    "onnxruntime-web/dist/ort-wasm-simd-threaded.jsep.mjs",
    "onnxruntime-web/dist/ort-wasm-simd-threaded.jsep.wasm",
    "three/build/three.module.js",
    "three/examples/jsm/controls/OrbitControls.js",
    "three/examples/jsm/loaders/GLTFLoader.js",
    "three/examples/jsm/utils/BufferGeometryUtils.js",
    "three/examples/jsm/libs/meshopt_decoder.module.js",
] + [
    f"three/examples/jsm/{f}"
    for f in (  # fx.js: the post chain and its imports
        "environments/RoomEnvironment.js",
        "math/SimplexNoise.js",
        "objects/Reflector.js",
        "postprocessing/EffectComposer.js",
        "postprocessing/GTAOPass.js",
        "postprocessing/MaskPass.js",
        "postprocessing/OutputPass.js",
        "postprocessing/Pass.js",
        "postprocessing/RenderPass.js",
        "postprocessing/SMAAPass.js",
        "postprocessing/ShaderPass.js",
        "postprocessing/UnrealBloomPass.js",
        "shaders/CopyShader.js",
        "shaders/GTAOShader.js",
        "shaders/LuminosityHighPassShader.js",
        "shaders/OutputShader.js",
        "shaders/PoissonDenoiseShader.js",
        "shaders/SMAAShader.js",
    )
]
PAGE = (
    "index.html",
    "diag.html",
    "diag_checks.json",
    "app.js",
    "sim.js",
    "shared.gen.js",
    "loader.js",
    "art.js",
    "fx.js",
    "audio.js",
    "broadcast.js",
    "contacts.js",
    "idle.js",
    "assets/arena.glb",
    "assets/tex/skin_detail.png",
)


def build_stamp():
    """The one version stamp: UTC yyMMdd of the HEAD commit (today, UTC, outside a git checkout)."""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%cd", "--date=format-local:%y%m%d"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            env={**os.environ, "TZ": "UTC"},
            check=True,
        ).stdout.strip()
        if re.fullmatch(r"\d{6}", out):
            return out
    except (OSError, subprocess.CalledProcessError):
        pass
    return time.strftime("%y%m%d", time.gmtime())


def build_commit():
    """The commit being deployed: GITHUB_SHA in CI, else git HEAD; empty outside a git checkout."""
    sha = os.environ.get("GITHUB_SHA", "").strip()
    if sha:
        return sha
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def matchup_files(loader):
    """Every matchup's arena and fingerprints, in its own models folder (a `dir: "v4/"` matchup reads models/v4/)."""
    files = set()
    for m in re.finditer(r'(\w+): \{ name: "[^"]+",(.*?)corners: \[(.*?)\] \}', loader, re.S):
        head, corners = m.group(2), m.group(3)
        d = re.search(r'dir: "([\w/]+)"', head)
        d = d[1] if d else ""
        xml = re.search(r'xml: "([\w.]+)"', head)[1]
        files.add(f"training/models/{d}{xml}")
        files.update(f"training/models/{d}{c}_fingerprint.json" for c in re.findall(r'char: "(\w+)"', corners))
    if not files:
        raise SystemExit("build.py: no matchups parsed from Web/loader.js")
    return sorted(files)


def check_diag_list():
    """/diag probes Web/diag_checks.json: every file it names is bundled, every bundled game file is probed."""
    checks = json.loads((WEB / "diag_checks.json").read_text(encoding="utf-8"))["checks"]
    named = set()
    page = (WEB / "index.html").read_text(encoding="utf-8")
    for c in checks:
        if c.get("probe") == "external":  # a third-party URL: it must be the one the game page really loads
            if c["url"].replace("&", "&amp;") not in page and c["url"] not in page:
                raise SystemExit(
                    f"Web/diag_checks.json: {c['id']} probes {c['url']}, which Web/index.html does not load"
                )
            continue
        for u in ([c["url"]] if "url" in c else []) + c.get("urls", []) + [c[k] for k in ("arena", "brain") if k in c]:
            if u != "./":
                named.add(os.path.normpath(os.path.join("Web", u)).replace(os.sep, "/"))
    missing = sorted(u for u in named if not (DIST / u).is_file())
    if missing:
        raise SystemExit("Web/diag_checks.json names files the bundle lacks: " + ", ".join(missing))
    # training/ (arenas, fingerprints, cards, brains) is probed by the "matchups" checks, straight from loader.js
    bundled = [p.relative_to(DIST).as_posix() for p in (DIST / "Web").rglob("*") if p.is_file()]
    unprobed = sorted(u for u in bundled if u not in named and not u.endswith((".html", "diag_checks.json")))
    if unprobed:
        raise SystemExit("bundled but not probed by /diag (add them to Web/diag_checks.json): " + ", ".join(unprobed))


def fail_lfs_pointers():
    """Ship-blocker: an unpulled git-LFS pointer looks like a tiny valid file but breaks browsers."""
    bad = [
        str(p.relative_to(DIST))
        for p in DIST.rglob("*")
        if p.is_file() and p.open("rb").read(64).startswith(b"version https://git-lfs")
    ]
    if bad:
        raise SystemExit("LFS pointers in dist (run 'git lfs pull'): " + ", ".join(bad))


STAMP = build_stamp()
COMMIT = build_commit()
loader = (WEB / "loader.js").read_text(encoding="utf-8")
chars = sorted(set(re.findall(r'char: "(\w+)"', loader)))
brains = sorted(set(re.findall(r'brain: "(\w+)"', loader)))
data = (
    matchup_files(loader)
    + [f"training/characters/{c}.json" for c in chars]
    + [f"training/models/brains/{b}.onnx" for b in brains]
    + [f for f in ["training/models/league.json"] if (ROOT / f).exists()]
)  # scouting (app.js reads it if present)

shutil.rmtree(DIST, ignore_errors=True)
for src, dst in (
    [(WEB / f, DIST / "Web" / f) for f in PAGE]
    + [(f, DIST / "Web" / "assets" / "audio" / f.name) for f in sorted((WEB / "assets" / "audio").glob("*.wav"))]
    + [(WEB / "assets" / f"{c}.glb", DIST / "Web" / "assets" / f"{c}.glb") for c in chars]
    + [(WEB / "node_modules" / f, DIST / "Web" / "node_modules" / f) for f in LIBS]
    + [(ROOT / f, DIST / f) for f in data]
):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
for html_name in ("index.html", "diag.html"):
    page = DIST / "Web" / html_name  # the stamp in the page footer: <span id="stamp">dev</span>
    text, n = re.subn(r'(id="stamp">)[^<]*(<)', rf"\g<1>{STAMP}\g<2>", page.read_text(encoding="utf-8"))
    if n != 1:
        raise SystemExit(f'build.py: {html_name} must carry exactly one id="stamp" element (found {n})')
    if html_name == "diag.html":  # /diag compares the live X-Commit header with this
        text, n = re.subn(r'(<meta name="po-commit" content=")[^"]*(")', rf"\g<1>{COMMIT}\g<2>", text)
        if n != 1:
            raise SystemExit('build.py: diag.html must carry exactly one <meta name="po-commit">')
    page.write_text(text, encoding="utf-8")
(DIST / "index.html").write_text(
    '<!doctype html><meta charset="utf-8"><title>PoSlap Arena</title>'
    '<meta http-equiv="refresh" content="0; url=Web/"><a href="Web/">PoSlap Arena</a>',
    encoding="utf-8",
)
# No Content-Security-Policy (user 2026-11-03): the old one sat under "headers", which Azure Static Web Apps does not
# read (it reads "globalHeaders"), so it was never served; served as written it blocks MuJoCo's WASM bindings (new Function).
# X-Commit on every response: the workflow and /diag check that the live site serves exactly this commit.
(DIST / "staticwebapp.config.json").write_text(
    json.dumps(
        {
            "mimeTypes": {
                ".wasm": "application/wasm",
                ".mjs": "text/javascript",
                ".onnx": "application/octet-stream",
                ".xml": "text/xml",
                ".glb": "model/gltf-binary",
            },
            "routes": [{"route": "/diag", "rewrite": "/Web/diag.html"}],
            "globalHeaders": {"X-Commit": COMMIT},
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)

fail_lfs_pointers()
check_diag_list()

size = sum(p.stat().st_size for p in DIST.rglob("*") if p.is_file())
print(
    f"Web/dist: {sum(1 for p in DIST.rglob('*') if p.is_file())} files, {size / 1e6:.1f} MB, v{STAMP}, commit {COMMIT[:7]} "
    f"(brains: {', '.join(brains)})"
)
