# One-time editor setup for the vendored URLab plugin (Plugins/UnrealRoboticsLab is part of this repo: nothing is cloned,
# patched or built here; see Plugins/UnrealRoboticsLab/README_VENDORED.md).
#
#   powershell -ExecutionPolicy Bypass -File scripts\setup_urlab.ps1
#
# URLab's MJCF importer preprocesses meshes with the engine's bundled Python and asks (a dialog) for these packages and
# for a Python path on its first import; under -unattended that dialog cancels the import. Install the packages and
# store the path (in the plugin's untracked Config/LocalUnrealRoboticsLab.ini), so the dialog never shows.
$ErrorActionPreference = 'Stop'
$root   = Split-Path -Parent $PSScriptRoot
$plugin = Join-Path $root 'Plugins/UnrealRoboticsLab'
$uePython = Join-Path $(if ($env:UE_ROOT) { $env:UE_ROOT } else { 'C:\Program Files\Epic Games\UE_5.8' }) 'Engine\Binaries\ThirdParty\Python3\Win64\python.exe'
if (Test-Path $uePython) {
    & $uePython -m pip install --disable-pip-version-check -q trimesh numpy scipy networkx Pillow
    $ini = Join-Path $plugin 'Config/LocalUnrealRoboticsLab.ini'
    [IO.File]::WriteAllText($ini, "[PythonSettings]`nPythonPath=$uePython`n")
    Write-Host "URLab Python: packages installed, path stored in $ini"
} else {
    Write-Warning "UE 5.8 bundled Python not found at ${uePython} (set UE_ROOT): MJCF imports in commandlets will cancel on the Python dialog"
}
