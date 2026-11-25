<#
.SYNOPSIS
  Restore the ready-made sentinel_shield database from db/sentinel_shield.dump.
  Use this on a NEW machine (e.g. the demo laptop): no internet and no data pipeline needed.

.DESCRIPTION
  Creates the database "sentinel_shield" if it does not exist, enables PostGIS, and restores
  all tables, rasters, functions and the materialized view. It only ever touches the
  sentinel_shield database. Reads the password from .env.

.EXAMPLE
  .\scripts\restore_db.ps1
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dump = Join-Path $root "db\sentinel_shield.dump"
if (-not (Test-Path $dump)) { throw "Missing $dump" }

$envFile = Join-Path $root ".env"
if (-not (Test-Path $envFile)) { throw ".env missing. Copy .env.example to .env and set DB_PASSWORD." }
$cfg = @{}
Get-Content $envFile | Where-Object { $_ -match "^\s*[^#].*=" } | ForEach-Object {
  $k, $v = $_ -split "=", 2; $cfg[$k.Trim()] = $v.Trim()
}
$env:PGPASSWORD = $cfg["DB_PASSWORD"]
$pgArgs = @("-h", $cfg["DB_HOST"], "-p", $cfg["DB_PORT"], "-U", $cfg["DB_USER"])
$db = "sentinel_shield"   # fixed on purpose: never touch any other database

$exists = & psql @pgArgs -d postgres -At -c "SELECT 1 FROM pg_database WHERE datname = '$db'"
if ($exists -ne "1") {
  Write-Host "Creating database $db ..." -ForegroundColor Cyan
  & psql @pgArgs -d postgres -c "CREATE DATABASE $db;"
}
& psql @pgArgs -d $db -q -c "CREATE EXTENSION IF NOT EXISTS postgis; CREATE EXTENSION IF NOT EXISTS postgis_raster;"

Write-Host "Restoring $dump into $db ..." -ForegroundColor Cyan
# Skip the EXTENSION entries (PostGIS is enabled above), restore everything else.
# --clean --if-exists replaces existing Sentinel Shield objects inside this database only.
$list = Join-Path $env:TEMP "sentinel_shield_restore.list"
& pg_restore -l $dump | Where-Object { $_ -notmatch " EXTENSION " } | Set-Content -Encoding ascii $list
& pg_restore @pgArgs -d $db --clean --if-exists --no-owner --no-privileges -L $list $dump
Remove-Item $list -ErrorAction SilentlyContinue
& psql @pgArgs -d $db -c "SELECT (SELECT count(*) FROM villages) AS villages, (SELECT count(*) FROM candidate_lz) AS landing_zones, (SELECT count(*) FROM flood_extent) AS flood_levels;"
Write-Host "Done. Start the app with .\scripts\start.ps1" -ForegroundColor Green
