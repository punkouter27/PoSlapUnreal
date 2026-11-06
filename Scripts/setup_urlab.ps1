# Clone, patch and build the URLab plugin (Plugins/UnrealRoboticsLab is not tracked in this repo).
# Safe to re-run: skips the clone and patch when the plugin folder already exists.
$ErrorActionPreference = 'Stop'
$root   = Split-Path -Parent $PSScriptRoot
$plugin = Join-Path $root 'Plugins/UnrealRoboticsLab'
$patch  = Join-Path $root 'Plugins/urlab_poslap.patch'

if (-not (Test-Path $plugin)) {
    git clone --recurse-submodules https://github.com/URLab-Sim/UnrealRoboticsLab $plugin
    git -C $plugin am $patch
    git -C $plugin submodule update --recursive   # the patch moves MuJoCo's gitlink to 3.10.0
}

# build_all.ps1 uses relative paths: it must run from its own folder.
Push-Location (Join-Path $plugin 'third_party')
try { powershell -ExecutionPolicy Bypass -File .\build_all.ps1 } finally { Pop-Location }
