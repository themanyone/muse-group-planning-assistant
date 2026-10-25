# Group Planning Assistant — native start script for Windows
# (roadmap B-001; kept in parity with run.sh). No Docker, ever:
# this runs the app directly under uvicorn.
# Usage: .\run.ps1 [-Demo]   (-Demo loads the demo seed first)
param([switch]$Demo)
Set-Location $PSScriptRoot

if ($Demo) {
    uv run python scripts/seed.py --demo
}

uv run uvicorn gpa.main:create_app --factory --host 127.0.0.1 --port 8000
