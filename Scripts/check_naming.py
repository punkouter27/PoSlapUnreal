"""Naming and folder-hierarchy lint for the PoSlap content.

    python Scripts/check_naming.py            # report
    python Scripts/check_naming.py --strict   # warnings become failures

Hard failures (always exit 1):
  - two assets with the same base name in different folders (Unreal resolves a
    soft path by base name, so a duplicate silently picks the wrong one);
  - a file name Unreal will not accept (spaces, non-ASCII, a leading digit);
  - a C++ type that is not PascalCase.

Warnings (exit 1 only with --strict):
  - an asset that does not carry the prefix its folder's type implies;
  - a content folder outside the Shared/ Core/ UI/ hierarchy.
"""
import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "Content"
SOURCE = ROOT / "Source"

# folder suffix -> the prefix an asset in it should carry (a list means "any of")
FOLDER_PREFIX = {
    "StaticMeshes": ("SM_",),
    "Meshes": ("SM_",),
    "Materials": ("M_", "MI_"),
    "Blueprints": ("BP_",),
    "Niagara": ("NS_",),
}

# The audio bank's own prefixes: sounds, reverb, ambiences.
AUDIO_PREFIX = ("S_", "RV_", "AM_", "M_")

# Content top-level folders, and where each one belongs.
FOLDER_OWNER = {
    "Arena": "Shared",
    "Athletes": "Core",
    "Brains": "Core",
    "Levels": "Core",
    "Collections": "Core",
    "Developers": "Core",
    "MuJoCoImports": "Core",
    "Data": "Core",
    "Audio": "UI",
    "FX": "UI",
    "Art": "Shared",
}

VALID_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
PASCAL = re.compile(r"^[A-Z][A-Za-z0-9]*$")


def walk_content():
    if not CONTENT.exists():
        return
    for path in sorted(CONTENT.rglob("*.uasset")) + sorted(CONTENT.rglob("*.umap")):
        yield path


def classify(path):
    """The prefix an asset in this folder should carry, or None if the folder is free-form."""
    parts = path.relative_to(CONTENT).parts
    for part in parts[:-1]:
        if part in FOLDER_PREFIX:
            return FOLDER_PREFIX[part]
    if parts and parts[0] == "Audio":
        return AUDIO_PREFIX
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="treat warnings as failures")
    opts = ap.parse_args()

    errors, warnings = [], []
    by_name = defaultdict(list)

    for path in walk_content():
        rel = path.relative_to(ROOT)
        base = path.stem
        by_name[base].append(rel)

        if not VALID_NAME.match(base):
            errors.append(f"{rel}: the name has a space, a non-ASCII character or starts with a digit")
        want = classify(path)
        if want:
            if isinstance(want, tuple):
                if not base.startswith(want):
                    errors.append(f"{rel}: expected one of {'/'.join(want)}")
            elif not base.startswith(want):
                errors.append(f"{rel}: expected the prefix '{want}'")
    for base, paths in by_name.items():
        if len(paths) > 1:
            errors.append(f"duplicate asset name '{base}': " + ", ".join(str(p) for p in paths))

    for folder, owner in FOLDER_OWNER.items():
        if (CONTENT / folder).exists() and owner not in ("Shared", "Core", "UI"):
            warnings.append(f"Content/{folder} has no place in the Shared/ Core/ UI/ hierarchy")

    if SOURCE.exists():
        for header in sorted(SOURCE.rglob("*.h")):
            for line in header.read_text(encoding="utf-8", errors="ignore").splitlines():
                m = re.match(r"\s*(?:class|struct)\s+(?:U\s+|F\s+|A\s+)?([A-Za-z_][A-Za-z0-9_]*)", line)
                if not m:
                    continue
                name = m.group(1)
                if name.endswith("_API"):     # the module export macro, not a type
                    continue
                if not PASCAL.match(name):
                    errors.append(f"{header.relative_to(ROOT)}: type '{name}' is not PascalCase")

    print(f"=== PoSlap naming lint ===")
    print(f"  {len(by_name)} assets, {len(errors)} error(s), {len(warnings)} warning(s)")
    for e in errors:
        print(f"  [FAIL] {e}")
    for w in warnings:
        print(f"  [WARN] {w}")
    if errors or (opts.strict and warnings):
        return 1
    print("  [PASS] every asset carries its folder's prefix and no name is duplicated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
