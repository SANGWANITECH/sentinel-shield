<#
.SYNOPSIS
  Start Sentinel Shield: FastAPI backend (port 8000) + React frontend (port 5173), then open the browser.

.EXAMPLE
  .\scripts\start.ps1            # development servers
  .\scripts\start.ps1 -Preview   # build once, then serve the optimised bundle (most robust for the demo)
  .\scripts\start.ps1 -NoBrowser # don't open the browser automatically
#>
param([switch]$Preview, [switch]$NoBrowser)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $py)) { throw "Python venv missing. See README: python -m venv .venv; pip install -r requirements.txt" }
if (-not (Test-Path (Join-Path $root "frontend\node_modules"))) { throw "Run 'npm install' inside frontend\ first." }
if (-not (Test-Path (Join-Path $root ".env"))) { throw ".env missing. Copy .env.example to .env and set DB_PASSWORD." }

$pg = Get-Service -Name "postgresql*" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($pg -and $pg.Status -ne "Running") { Write-Warning "PostgreSQL service '$($pg.Name)' is $($pg.Status). Start it first." }

Write-Host "Starting backend on http://127.0.0.1:8000 ..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList @(
  "-NoExit", "-Command",
  "`$host.UI.RawUI.WindowTitle='Sentinel Shield API'; & '$py' -m uvicorn app.main:app --app-dir '$root\backend' --host 127.0.0.1 --port 8000"
)

$ok = $false
for ($i = 0; $i -lt 40; $i++) {
  Start-Sleep -Milliseconds 500
  try { $h = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/health" -TimeoutSec 2; $ok = $true; break } catch { }
}
if ($ok) { Write-Host ("Backend OK: PostGIS {0}, {1} flood levels, {2} landing zones" -f $h.postgis, $h.flood_levels, $h.candidate_lz) -ForegroundColor Green }
else { Write-Warning "Backend did not answer /api/health yet. Check the 'Sentinel Shield API' window." }

if ($Preview) {
  Write-Host "Building frontend ..." -ForegroundColor Cyan
  Push-Location (Join-Path $root "frontend"); npm run build; Pop-Location
  $cmd = "npm run preview"; $url = "http://localhost:4173"
} else {
  $cmd = "npm run dev"; $url = "http://localhost:5173"
}
Write-Host "Starting frontend ($cmd) ..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList @(
  "-NoExit", "-Command",
  "`$host.UI.RawUI.WindowTitle='Sentinel Shield UI'; Set-Location '$root\frontend'; $cmd"
)
Start-Sleep -Seconds 4
if (-not $NoBrowser) { Start-Process $url }
Write-Host "Sentinel Shield running at $url  (close the two server windows to stop)" -ForegroundColor Green
