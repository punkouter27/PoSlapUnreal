"""Imports every character's voice pack (Scripts/voice_pack.py) into /Game/Audio/Voice/<card>/.

    UnrealEditor-Cmd.exe PoSlapUnreal.uproject -run=pythonscript -script="<abs>/Scripts/voice_import.py" -unattended

Content/Art/Exports/Audio/Voice/<card>/<EMOTION>_<sound>_<level>.wav -> /Game/Audio/Voice/<card>/S_V_<card>_<EMOTION>_<sound>_<level>
(the card in the name: Unreal resolves soft paths by base name, so no two cards may share one; check_naming.py).
ASlapHUD::Emote loads them by that path; /Game/Audio is always cooked (DefaultGame.ini), so a phone build has them.
Set SLAP_VOICE_CARD=<card> to import one card only.
Also the ring announcer (Scripts/announcer.py): Content/Art/Exports/Audio/Announcer/<key>.wav -> /Game/Audio/Announcer/S_A_<key>
(skipped with SLAP_VOICE_CARD).
"""
import os

import unreal

PROJ = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir())
SRC = os.path.join(PROJ, "Content", "Art", "Exports", "Audio", "Voice")
ASSETS = unreal.EditorAssetLibrary


def log(msg):
    unreal.log(f"voice_import: {msg}")


def main():
    only = os.environ.get("SLAP_VOICE_CARD")
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    total = 0
    for card in sorted(os.listdir(SRC)):
        folder = os.path.join(SRC, card)
        if not os.path.isdir(folder) or (only and card != only):
            continue
        tasks = []
        for f in sorted(os.listdir(folder)):
            if not f.endswith(".wav"):
                continue
            t = unreal.AssetImportTask()
            t.filename = os.path.join(folder, f)
            t.destination_path = f"/Game/Audio/Voice/{card}"
            t.destination_name = f"S_V_{card}_{f[:-4]}"
            t.automated, t.replace_existing, t.save = True, True, True
            tasks.append(t)
        tools.import_asset_tasks(tasks)
        ok = sum(ASSETS.does_asset_exist(f"{t.destination_path}/{t.destination_name}") for t in tasks)
        total += ok
        log(f"{card}: {ok}/{len(tasks)} sounds in /Game/Audio/Voice/{card}")
    ann = os.path.join(PROJ, "Content", "Art", "Exports", "Audio", "Announcer")
    if not only and os.path.isdir(ann):
        tasks = []
        for f in sorted(os.listdir(ann)):
            if not f.endswith(".wav"):
                continue
            t = unreal.AssetImportTask()
            t.filename = os.path.join(ann, f)
            t.destination_path, t.destination_name = "/Game/Audio/Announcer", f"S_A_{f[:-4]}"
            t.automated, t.replace_existing, t.save = True, True, True
            tasks.append(t)
        tools.import_asset_tasks(tasks)
        ok = sum(ASSETS.does_asset_exist(f"/Game/Audio/Announcer/{t.destination_name}") for t in tasks)
        total += ok
        log(f"announcer: {ok}/{len(tasks)} lines in /Game/Audio/Announcer")
    log(f"done: {total} sounds")


if __name__ == "__main__":
    main()
