# Clone, patch and build the URLab plugin. Plugins/UnrealRoboticsLab is NOT tracked in this repo (its own git repo,
# ~2.3 GB with build output; ignored in .gitignore): this script recreates it from upstream + Plugins/urlab_poslap.patch,
# so a fresh clone builds the editor target after one run.
#
#   powershell -ExecutionPolicy Bypass -File scripts\setup_urlab.ps1
#
# Safe to re-run: skips the clone and patch when the plugin folder already exists.
$ErrorActionPreference = 'Stop'
$root   = Split-Path -Parent $PSScriptRoot
$plugin = Join-Path $root 'Plugins/UnrealRoboticsLab'
$patch  = Join-Path $root 'Plugins/urlab_poslap.patch'

if (-not (Test-Path $plugin)) {
    git clone --recurse-submodules https://github.com/URLab-Sim/UnrealRoboticsLab $plugin
    if ($LASTEXITCODE -ne 0) { throw "git clone failed ($LASTEXITCODE)" }
    git -C $plugin -c user.name=PoSlap -c user.email=poslap@localhost am --3way $patch
    if ($LASTEXITCODE -ne 0) { throw "git am $patch failed ($LASTEXITCODE)" }
    git -C $plugin submodule update --recursive   # the patch moves MuJoCo's gitlink to 3.10.0
    if ($LASTEXITCODE -ne 0) { throw "submodule update failed ($LASTEXITCODE)" }
}

# CoACD's build.ps1 hard-codes the "Visual Studio 17 2022" CMake generator; on a machine with only a newer Visual Studio
# (e.g. 18 2026) point it at the newest installed one.
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (Test-Path $vswhere) {
    $vsMajor = (& $vswhere -latest -products * -property installationVersion).Split('.')[0]
    $gen = (cmake --help | Select-String "Visual Studio $vsMajor \d{4}" | Select-Object -First 1).Matches.Value
    $coacd = Join-Path $plugin 'third_party/CoACD/build.ps1'
    if ($gen -and $vsMajor -ne '17') {
        (Get-Content $coacd -Raw) -replace '"Visual Studio \d+ \d{4}"', "`"$gen`"" | Set-Content $coacd -NoNewline
    }
}

# build_all.ps1 uses relative paths: it must run from its own folder. It does not stop on a failed dependency, so
# check that every dependency's install folder exists afterwards.
Push-Location (Join-Path $plugin 'third_party')
try { powershell -ExecutionPolicy Bypass -File .\build_all.ps1 } finally { Pop-Location }
foreach ($dep in 'CoACD', 'MuJoCo', 'libzmq') {
    if (-not (Test-Path (Join-Path $plugin "third_party/install/$dep/INSTALLED_SHA.txt"))) { throw "URLab third-party build failed: $dep not installed" }
}

# URLab's MJCF importer preprocesses meshes with the engine's bundled Python and asks (a dialog) for these packages and
# for a Python path on its first import; under -unattended that dialog cancels the import. Install the packages and
# store the path (in the plugin's untracked Config/LocalUnrealRoboticsLab.ini), so the dialog never shows.
$uePython = Join-Path $(if ($env:UE_ROOT) { $env:UE_ROOT } else { 'C:\Program Files\Epic Games\UE_5.8' }) 'Engine\Binaries\ThirdParty\Python3\Win64\python.exe'
if (Test-Path $uePython) {
    & $uePython -m pip install --disable-pip-version-check -q trimesh numpy scipy networkx Pillow
    $ini = Join-Path $plugin 'Config/LocalUnrealRoboticsLab.ini'
    [IO.File]::WriteAllText($ini, "[PythonSettings]`nPythonPath=$uePython`n")
    Write-Host "URLab Python: packages installed, path stored in $ini"
} else {
    Write-Warning "UE 5.8 bundled Python not found at ${uePython} (set UE_ROOT): MJCF imports in commandlets will cancel on the Python dialog"
}
