"""Item 12 (2026-11-04): list /Game assets nothing references (report only, deletes nothing).

UnrealEditor-Cmd.exe PoSlapUnreal.uproject -run=pythonscript -script="<abs>/Scripts/unused_assets.py" -unattended
Writes Saved/unused_assets.json. Levels are roots; an asset is "unused" when no package references it, directly or by
soft path. Assets the C++ loads by path at runtime (FX, audio, brains, materials named in Source/) are checked too:
any asset whose name appears in Source/ or Scripts/ is reported as "loaded by code", not unused.
"""
import json, os, re, unreal

root = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir())
reg = unreal.AssetRegistryHelpers.get_asset_registry()
reg.search_all_assets(True)
opts = unreal.AssetRegistryDependencyOptions(include_soft_package_references=True, include_hard_package_references=True,
                                             include_searchable_names=False, include_soft_management_references=True,
                                             include_hard_management_references=True)
code = ""
for d in ("Source", "Scripts", "Config"):
    for dp, _, fs in os.walk(os.path.join(root, d)):
        for f in fs:
            if f.endswith((".cpp", ".h", ".py", ".ini")):
                code += open(os.path.join(dp, f), encoding="utf-8", errors="ignore").read()
unused, by_code, total = [], [], 0
for a in reg.get_assets_by_path("/Game", recursive=True):
    pkg = str(a.package_name)
    if str(a.asset_class_path.asset_name) in ("World", "MapBuildDataRegistry"):
        continue
    total += 1
    refs = [r for r in (reg.get_referencers(pkg, opts) or []) if str(r) != pkg]
    if refs:
        continue
    name = pkg.rsplit("/", 1)[-1]
    (by_code if re.search(r"\b" + re.escape(name) + r"\b", code) else unused).append(pkg)
size = lambda p: os.path.getsize(f) if os.path.exists(f := os.path.join(root, "Content", p[len("/Game/"):] + ".uasset")) else 0
out = {"assets": total, "loaded_by_code": sorted(by_code),
       "unused": sorted(({"package": p, "bytes": size(p)} for p in unused), key=lambda e: -e["bytes"])}
out["unused_mb"] = round(sum(e["bytes"] for e in out["unused"]) / 1e6, 1)
json.dump(out, open(os.path.join(root, "Saved", "unused_assets.json"), "w"), indent=1)
unreal.log(f"unused assets: {len(unused)} of {total} ({out['unused_mb']} MB), {len(by_code)} more loaded by code only")
