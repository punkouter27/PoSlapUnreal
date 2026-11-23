# Opens the training Benchmark page (benchmark/page.html) for this repo: one button runs ~10 s of the real training
# loop and records step time, machine and machine load in benchmark/history.json (commit it to compare laptops).
# -Run: no page; runs one Benchmark in this console, records it in benchmark/history.json and exits 0 (recorded) or 1.
$ErrorActionPreference = 'Stop'
$py = Join-Path $PSScriptRoot 'training/.venv/Scripts/python.exe'
if (-not (Test-Path $py)) {
    Write-Host 'Creating the training environment (README: uv venv + requirements + CUDA torch)...'
    Push-Location $PSScriptRoot
    uv venv training/.venv --python 3.12
    uv pip install -p training/.venv -r training/requirements.txt
    uv pip install -p training/.venv torch --index-url https://download.pytorch.org/whl/cu128
    Pop-Location
}
& $py (Join-Path $PSScriptRoot 'benchmark/serve.py') @args
exit $LASTEXITCODE
