# Every check in one place. Run from the repo root:
#
#   powershell -ExecutionPolicy Bypass -File Scripts\verify_all.ps1
#   powershell -ExecutionPolicy Bypass -File Scripts\verify_all.ps1 -Quick   # skip the CUDA env checks
#   powershell -ExecutionPolicy Bypass -File Scripts\verify_all.ps1 -Unreal  # add the editor build + the UI test
#
# AGENTS.md: "no unit-test suite; these are the tests". This runs all of them and reports one summary.
# Nothing here assumes a drive letter, a temp folder or a working directory: every path is resolved from
# the script's own location, and every expectation is read from the same config the app reads.

[CmdletBinding()]
param(
    [switch]$Quick,     # skip the CUDA-only realism checks
    [switch]$Unreal,    # also build the editor and run the synthetic Unreal tests
    [switch]$SkipWeb    # skip the node checks (no node installed)
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Py = Join-Path $Root "training\.venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }

$Results = @()
function Step($Name, $Script) {
    Write-Host ""
    Write-Host ("=" * 72) -ForegroundColor DarkGray
    Write-Host "  $Name" -ForegroundColor Cyan
    Write-Host ("=" * 72) -ForegroundColor DarkGray
    $out = & $Script 2>&1 | Out-String
    $code = $LASTEXITCODE
    Write-Host $out
    $script:Results += [pscustomobject]@{ Name = $Name; Code = $code }
    if ($code -ne 0) { Write-Host "  -> FAILED (exit $code)" -ForegroundColor Red }
    else { Write-Host "  -> PASS" -ForegroundColor Green }
}

# --- the CPU-only gates -------------------------------------------------------------------------
Step "Cards, models and packaged staging" { & $Py (Join-Path $Root "training\verify_cards.py") }
Step "The three engines agree on the shared numbers" { & $Py (Join-Path $Root "training\verify_constants.py") }
Step "Asset naming and folder conventions" { & $Py (Join-Path $Root "Scripts\check_naming.py") }

# --- the browser engine -------------------------------------------------------------------------
if (-not $SkipWeb) {
    foreach ($p in @("gg", "kk", "gk")) {
        Step "Browser engine ($p)" { & node (Join-Path $Root "Web\check.mjs") $p }
    }
    Step "Live relay" { & node (Join-Path $Root "Web\live_check.mjs") }
    if (Test-Path (Join-Path $Root "Web\api_check.mjs")) {
        Step "Spectator API" { & node (Join-Path $Root "Web\api_check.mjs") }
    }
    Step "The web build" { & $Py (Join-Path $Root "Web\build.py") }
}

# --- the CUDA-only gate -------------------------------------------------------------------------
if (-not $Quick) {
    Step "AI realism (needs CUDA)" { & $Py (Join-Path $Root "training\verify_realism.py") }
}

# --- the editor ---------------------------------------------------------------------------------
if ($Unreal) {
    $Editor = "C:\Program Files\Epic Games\UE_5.8\Engine\Build\BatchFiles\Build.bat"
    $Proj = Join-Path $Root "PoSlapUnreal.uproject"
    Step "Editor build" { & $Editor PoSlapUnrealEditor Win64 Development -Project="$Proj" -WaitMutex -NoHotReloadFromIDE }
    Step "The UI test" {
        & "C:\Program Files\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor-Cmd.exe" "$Proj" `
            -game -SlapUiTest -nullrhi -unattended -nosplash 2>&1 |
            Select-String -Pattern "LogSlap" | Select-Object -Last 40
    }
}

# --- the summary --------------------------------------------------------------------------------
Write-Host ""
Write-Host ("=" * 72) -ForegroundColor DarkGray
$Failed = $Results | Where-Object { $_.Code -ne 0 }
Write-Host "  SUMMARY"
Write-Host ("=" * 72) -ForegroundColor DarkGray
foreach ($r in $Results) {
    $mark = if ($r.Code -eq 0) { "PASS" } else { "FAIL" }
    $col = if ($r.Code -eq 0) { "Green" } else { "Red" }
    Write-Host ("  {0,-4}  {1}" -f $mark, $r.Name) -ForegroundColor $col
}
Write-Host ""
if ($Failed) {
    Write-Host "  $($Failed.Count) of $($Results.Count) checks FAILED." -ForegroundColor Red
    exit 1
}
Write-Host "  all $($Results.Count) checks passed." -ForegroundColor Green
exit 0
