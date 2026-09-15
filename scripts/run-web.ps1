# Goal xG — start FastAPI web UI on Windows (PowerShell).
# Usage (from repo root or anywhere):
#   powershell -ExecutionPolicy Bypass -File .\scripts\run-web.ps1
# Or:  .\scripts\run-web.bat
#
# Set GOAL_API_KEY in .env before expecting live data (never commit real keys).

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

Write-Host "goal-xg web — working directory: $Root"

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating .venv ..."
    python -m venv .venv
}

Write-Host "Activating .venv ..."
& .\.venv\Scripts\Activate.ps1

Write-Host "Installing package (editable + dev,live) ..."
python -m pip install -U pip
pip install -e ".[dev,live]"

if (-not (Test-Path ".env")) {
    if (Test-Path ".env.example") {
        Write-Host "Copying .env.example -> .env (edit and set GOAL_API_KEY)"
        copy .env.example .env
    } else {
        Write-Warning ".env.example missing; create .env with GOAL_API_KEY=..."
    }
} else {
    Write-Host ".env already present — not overwriting"
}

Write-Host ""
Write-Host "Reminder: set GOAL_API_KEY in .env for live GOAL REST/WS data."
Write-Host "Starting uvicorn at http://127.0.0.1:8000 ..."
Write-Host ""

uvicorn goal_xg.web.app:app --host 127.0.0.1 --port 8000
