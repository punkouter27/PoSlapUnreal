# Pixel Streaming: watch the match in a browser (http://localhost/ on this machine, http://<this PC's IP>/ on the LAN).
#
#   powershell -ExecutionPolicy Bypass -File Scripts/stream.ps1 [-Brain training/models/brains/<x>.onnx] [-BlueBrain ...]
#
# First run: clones Epic's signalling server + web player (PixelStreamingInfrastructure, branch UE5.8; UE 5.8's own
# get_ps_servers.bat still defaults to UE5.7) into Saved/PixelStreaming; its start script installs Node packages. Then starts the
# signalling server (players on :80, the game on :8888) and the game off-screen at 540x960 streaming to it.
param([string]$Brain = "", [string]$BlueBrain = "")
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
$Infra = Join-Path $Root "Saved\PixelStreaming"
if (-not (Test-Path $Infra)) {
    git clone --depth 1 -b UE5.8 https://github.com/EpicGamesExt/PixelStreamingInfrastructure $Infra
}
$Signal = Join-Path $Infra "SignallingWebServer\platform_scripts\cmd\start.bat"
Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "`"$Signal`"" -WorkingDirectory (Split-Path $Signal) -WindowStyle Minimized

$GameArgs = @("`"$Root\PoSlapUnreal.uproject`"", "-game", "-RenderOffScreen", "-ResX=540", "-ResY=960", "-ForceRes",
          "-PixelStreamingConnectionURL=ws://127.0.0.1:8888", "-log=slap_stream.log", "-SlapLoop")   # -SlapLoop: match after match, unattended
if ($Brain) { $GameArgs += "-SlapRedBrain=$Brain" }
if ($BlueBrain) { $GameArgs += "-SlapBlueBrain=$BlueBrain" }
Start-Process -FilePath "C:\Program Files\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor.exe" -ArgumentList $GameArgs
Write-Host "Streaming: open http://localhost/ (first run: the signalling window installs packages for a minute or two)."
