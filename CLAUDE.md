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
Tables: `district_boundary`, `study_area`, `rivers`, `roads`, `villages` (name, population),
`buildings`, `power_lines`, `flood_extent` (level_m unique), `candidate_lz` (code, area_m2,
mean_slope, landcover), `staging_base`. Every geometry column is SRID 32736 with a GIST index.
Apply with: `psql -U postgres -d sentinel_shield -f db/schema.sql`

## Working conventions
- Team members must be able to explain every file in the presentation: when creating/changing
  a file, explain in 2–3 plain sentences what it does and why.
- Keep data small and clipped to the study area.
