# Sentinel Shield — project context

University GIS project for **COM413 Spatial Analysis, UNIMA**.
**Repo due: 1 Dec 2026. Live demo: 2 Dec 2026.**

## What it does
A database-driven web app that finds **safe helicopter landing zones (LZs)** for cyclone/flood
rescue in **Dowa District, Malawi**, focused on the **Kasangadzi River area around Mponela**
(real flash floods there in January 2024).

A **flood-level slider (0–6 m)** simulates rising water. For each level the app shows:
- the flood extent
- stranded villages (inside the flood extent)
- safe landing zones
- a link from each stranded village to its nearest safe LZ, with distance and helicopter ETA
- villages with **no safe LZ within 1.3 km**

### Safe landing zone criteria
Flat (low slope), open land cover, dry (outside flood extent at the current level),
clear of buildings and power lines, **at least 50 x 50 m**, **at least 120 m from water**.

### Rescue assumptions (configurable, in `config.toml`)
- **Staging base:** Kamuzu International Airport, Lilongwe (lat -13.789, lon 33.781) -> `staging_base` table.
- **Helicopter cruise speed:** 180 km/h (used for ETA).

## Spatial methods
Flood simulation from DEM, slope analysis, land-cover reclassification, buffers,
multi-criteria suitability, spatial intersection, nearest-facility (KNN).

## Architecture / design decisions
- **Heavy raster work is precomputed offline** in QGIS/Python (DEM -> flood polygons per level,
  slope, land-cover reclass, suitability -> candidate LZ polygons). Results are loaded into PostGIS as vectors.
- **PostGIS answers each slider level live** with vector queries:
  `ST_Difference` (candidate LZs minus flood/buffer), `ST_Intersects` (stranded villages),
  KNN `<->` with `LATERAL` (nearest safe LZ per village).
- Store all geometry in **EPSG:32736 (WGS 84 / UTM 36S)** so distances/areas are in metres.
  Return **EPSG:4326 GeoJSON** to the frontend (`ST_Transform(geom, 4326)`).

## Stack and environment
- Windows 11, PowerShell. **Slow internet**: avoid large downloads, prefer small clipped data.
- Local PostgreSQL 17 + PostGIS 3.7. DB `sentinel_shield`, user `postgres`, host `localhost`, port `5432`.
  Extensions `postgis` and `postgis_raster` enabled. Credentials in `.env` (never commit it; see `.env.example`).
- Python 3.12, Node 26, Git. Remote: GitHub `SANGWANITECH/sentinel-shield`.
- Backend: **FastAPI + psycopg**. Frontend: **React (Vite) + Leaflet**, dark "command centre" UI.

## Repo layout
```
data/raw        source downloads (git-ignored)
data/processed  clipped/cleaned layers ready to load
db/             schema.sql and other SQL
scripts/        Python/PowerShell processing + loading scripts
backend/        FastAPI app
frontend/       React + Leaflet app
qgis/           QGIS project files / models
docs/           report, diagrams, presentation notes
```

## Database (db/schema.sql)
Tables: `district_boundary`, `study_area`, `rivers`, `roads`, `villages` (name, population, footprint),
`buildings`, `power_lines`, `flood_extent` (level_m unique), `candidate_lz` (code, area_m2,
mean_slope, landcover, score, pad), `staging_base`. Every geometry column is SRID 32736 with a GIST index.
Raster tables `dem` (Copernicus GLO-30, 30 m) and `landcover` (ESA WorldCover 2021, 10 m) are
created by `scripts/load_data.py` via raster2pgsql.
Apply with: `psql -U postgres -d sentinel_shield -f db/schema.sql`

## Data pipeline (session 2)
- Study area bbox (EPSG:4326, W,S,E,N): `33.6909, -13.5657, 33.7833, -13.4937` (10 x 8 km on Mponela).
  The Kasangadzi River could not be confirmed by name in OSM; news reports place it through Mponela.
- `config.toml`: study area, rescue assumptions, persons_per_building = 4.4, source URLs.
- `scripts/common.py`: paths, config + .env readers.
- `scripts/download_data.py`: data/raw downloads (skips existing files; `--force`, `--only`).
  OSM via Overpass mirrors, with an OSM API bbox fallback. Rasters via windowed COG reads.
- `scripts/load_data.py`: reproject to 32736, clip, estimate population (Voronoi x buildings), then
  TRUNCATE + reload in one transaction. Rasters are reprojected into data/processed and loaded with raster2pgsql.
- Run with `.venv\Scripts\python.exe`. Network here is slow AND intermittent, and Overpass is often overloaded.
  Python's own TLS handshakes stall, so all HTTP goes through **curl** (with resume). pip index pages time out,
  so install with `scripts/fetch_wheels.py` + `requirements.lock` (offline `pip install --no-index`).

## State after the overnight run (session 3), all phases built (see PROGRESS.md)
- OSM is thin here: 1 place node, 831 buildings, 9 unnamed river ways (treated as the Kasangadzi), 0 power lines.
  Added Microsoft ML building footprints (+14,110). 49 settlements derived from building clusters
  (`config.toml [settlements]`), each with a `footprint` polygon used for the flood test.
- `scripts/process_terrain.py`: slope, reclass, suitability -> 40 LZs (`candidate_lz` + `pad` landing point + `score`);
  HAND floods 0-6 m (`flood_extent`, level 0 = normal channel); hillshade -> `frontend/public/basemap/`; GPKG export.
- `db/functions.sql`: `sentinel_scenario(level)` and `sentinel_base_layers()` (jsonb, EPSG:4326), plus the materialized
  view `flood_extent_parts` (ST_Subdivide). 40-84 ms per level.
- `backend/app/main.py` (FastAPI, port 8000) + 7 pytest tests. `frontend/` (React + Vite + Leaflet, port 5173 dev / 4173 preview).
- `scripts/start.ps1 [-Preview] [-NoBrowser]` starts everything. `scripts/restore_db.ps1` restores `db/sentinel_shield.dump`.
- Visual checks: headless Edge `--screenshot` with `--force-prefers-reduced-motion` and `?level=X` in the URL.
- Only use the `sentinel_shield` database (another, unrelated database lives on this server).

## Working conventions
- Team members must be able to explain every file in the presentation: when creating/changing
  a file, explain in 2–3 plain sentences what it does and why.
- Keep data small and clipped to the study area.
