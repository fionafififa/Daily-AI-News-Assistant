param([ValidateRange(1024,65535)][int]$Port = 8765)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'Install uv first, or follow the Python venv instructions in README.md.'
}
Write-Host "Daily AI News: http://127.0.0.1:$Port"
uv run --locked --no-dev --python 3.12 python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port $Port
exit $LASTEXITCODE
