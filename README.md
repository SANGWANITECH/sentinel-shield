# Sentinel Shield

Finding safe helicopter landing zones for flood rescue in Dowa District, Malawi.
*COM413 Spatial Analysis, University of Malawi (UNIMA).*

## Problem
In January 2024 flash floods on the Kasangadzi River near Mponela cut villages off.
Rescue helicopters need somewhere safe to land: ground that is flat, open and dry, with no
buildings or power lines. Where those places are changes as the water rises, and finding
them by hand during an emergency is slow.

## Solution
A web app with a **flood-level slider (0 to 6 m)**. For each water level it shows:
- the flood extent, the villages it strands and the roads it cuts
- the landing zones that are still safe, and those lost to the flood
- a line from each stranded village to its nearest safe zone, with distance and helicopter ETA
- villages with **no safe zone within 1.3 km** (red, "send boat team")

A landing zone is **safe** when it is flat (<= 7°), open (grass, crop or bare), at least **50 x 50 m**,
at least 30 m from buildings and 50 m from power lines, and at least **120 m from flood water**.

## Run it (Windows, PowerShell)
**If everything is already installed on this machine:**
```powershell
.\scripts\start.ps1 -Preview      # starts the API + UI and opens http://localhost:4173
```
`.\scripts\start.ps1` (without `-Preview`) starts the development server on http://localhost:5173 instead.
Close the two server windows to stop. The app makes **no internet requests** at runtime.

**On a new machine (fast path, no data pipeline):**
1. Install PostgreSQL 17 + PostGIS, Python 3.12 and Node.js.
2. `Copy-Item .env.example .env`, then set `DB_PASSWORD`.
3. `.\scripts\restore_db.ps1` restores the ready-made database from `db/sentinel_shield.dump` (2 MB).
4. Python packages:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   # on a very slow line, use the resumable installer instead:
   python scripts\fetch_wheels.py; .\.venv\Scripts\python.exe -m pip install --no-index --find-links .wheelhouse -r requirements.lock
   ```
5. `cd frontend; npm install; cd ..`
6. `.\scripts\start.ps1 -Preview`

**Rebuild everything from the raw data (full pipeline):**
```powershell
psql -U postgres -d sentinel_shield -f db/schema.sql
.\.venv\Scripts\python.exe scripts\download_data.py    # clipped downloads into data/raw (skips existing files)
.\.venv\Scripts\python.exe scripts\load_data.py        # reproject, clip, settlements, population -> PostGIS
.\.venv\Scripts\python.exe scripts\process_terrain.py  # slope, suitability, landing zones, HAND floods, hillshade
psql -U postgres -d sentinel_shield -f db/functions.sql
```
**Tests:** `cd backend; ..\.venv\Scripts\python.exe -m pytest -q`

## Architecture
```
 OFFLINE (minutes, run once)                         LIVE (each slider move, ~50 ms)
 ───────────────────────────                         ───────────────────────────────
 download_data.py  ─► data/raw                       React + Leaflet  (frontend/)
   OSM, Microsoft buildings, geoBoundaries,               │  GET /api/scenario/3.5
   Copernicus DEM + WorldCover (windowed reads)           ▼
 load_data.py      ─► PostGIS tables (EPSG:32736)    FastAPI + psycopg (backend/)
   settlements from building clusters,                    │  SELECT sentinel_scenario(3.5)
   Voronoi population                                     ▼
 process_terrain.py ─► candidate_lz, flood_extent    PostGIS (db/functions.sql)
   slope, reclass, buffers, suitability, HAND             ST_DWithin · ST_Intersects ·
                                                          LATERAL + KNN <-> · ST_Transform → GeoJSON
```

## Spatial methods
| Method | Where |
|---|---|
| Slope analysis (Copernicus 30 m DEM) | offline, `process_terrain.py` |
| Land-cover reclassification (ESA WorldCover) | offline |
| Buffers (buildings 30 m, power lines 50 m) | offline |
| Multi-criteria suitability (Boolean overlay) + 50 x 50 m erosion test | offline, giving `candidate_lz` |
| Flood simulation: HAND (height above nearest drainage), 0 to 6 m | offline, giving `flood_extent` |
| Buffer / proximity to flood (`ST_DWithin` 120 m) | **live**, PostGIS |
| Spatial intersection (stranded villages, cut roads) | **live**, `ST_Intersects`, `ST_Intersection` |
| Nearest facility (KNN) | **live**, `LEFT JOIN LATERAL ... ORDER BY pad <-> geom LIMIT 1` |

Geometry is stored in **EPSG:32736 (UTM 36S)** so all measurements are in metres. The API returns
**EPSG:4326 GeoJSON**. Every geometry column has a GIST index. Details: [docs/METHODS.md](docs/METHODS.md),
[docs/DATABASE.md](docs/DATABASE.md), [docs/CODE_GUIDE.md](docs/CODE_GUIDE.md).

## Data sources
| Layer | Source | How it is fetched |
|---|---|---|
| Dowa boundary | geoBoundaries MWI ADM2 (simplified) | filtered to Dowa |
| Roads, rivers, place names, buildings | OpenStreetMap | Overpass API per layer, clipped (OSM API fallback) |
| Extra buildings | Microsoft Global ML Building Footprints | the 2 tiles covering the area, clipped |
| Elevation (DEM) | Copernicus GLO-30 (AWS open data) | windowed read of the study area only |
| Land cover | ESA WorldCover 10 m 2021 (AWS open data) | windowed read of the study area only |

**Study area:** 10 x 8 km centred on Mponela, bbox `33.6909, -13.5657, 33.7833, -13.4937` (W, S, E, N, EPSG:4326).

## Assumptions
All assumptions are set in `config.toml` and can be changed there.
- **Kasangadzi River:** OSM has no river with that name. The unnamed river crossing the M1 just north of Mponela is treated as the Kasangadzi.
- **Settlements:** OSM has only one place node here, so settlements are derived from building clusters
  (buildings within 100 m, at least 40 buildings, the town split into 1 km sectors). This gives 49 settlements.
- **Village population.** OSM rarely records population, so we estimate it. Each village gets a
  Voronoi polygon (the area closer to it than to any other village). We count the buildings in
  that polygon and multiply by **4.4 persons per building**. This assumes one building is roughly one
  household, using the average household size from the 2018 Malawi Population and Housing Census.
  *Limitations:* non-residential buildings are counted, compounds with several buildings are
  overcounted, unmapped buildings are missed, and villages at the edge of the study area have cut-off cells.
- **Helicopter staging base:** Kamuzu International Airport, Lilongwe (-13.789, 33.781).
- **Helicopter cruise speed:** 180 km/h. ETA = straight-line distance from KIA to the landing spot / speed.
- **Flood model:** HAND is a simplified terrain-based model, **not a hydraulic model** (see docs/METHODS.md).

## Stack
PostgreSQL 17 + PostGIS 3.7 · Python 3.12 (geopandas, rasterio, shapely) · FastAPI + psycopg 3 ·
React 18 + Vite 6 + Leaflet 1.9 · QGIS (manual reproduction: [qgis/README.md](qgis/README.md))

## Repository layout
```
config.toml     all thresholds and assumptions
data/raw        downloads (git-ignored)      data/processed  GeoPackage, hillshade, rasters
db/             schema.sql, functions.sql, sentinel_shield.dump
scripts/        download, load, terrain processing, start/restore scripts
backend/        FastAPI app + pytest tests   frontend/  React + Leaflet app
qgis/           manual QGIS steps            docs/      methods, database, code guide, demo, slide, Q&A
```

## Presentation material
**New to the project? Start with [docs/TEAM_GUIDE.md](docs/TEAM_GUIDE.md).**
[docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) (3-minute demo) · [docs/SLIDE.md](docs/SLIDE.md) ·
[docs/QA_PREP.md](docs/QA_PREP.md) (25 likely questions) · [PROGRESS.md](PROGRESS.md) (build log and decisions)
