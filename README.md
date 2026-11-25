# Sentinel Shield

Finding safe helicopter landing zones for flood rescue in Dowa District, Malawi.
*COM413 Spatial Analysis, University of Malawi (UNIMA).*

## Problem
In January 2024 flash floods on the Kasangadzi River near Mponela cut villages off.
Rescue helicopters need somewhere safe to land: ground that is flat, open and dry, with no
buildings or power lines. Where those places are changes as the water rises, and finding
them by hand during an emergency is slow.

## Solution
A web app with a **flood-level slider (0–6 m)**. For each water level it shows:
- the flood extent and the villages it strands
- the landing zones that are still safe
- a line from each stranded village to its nearest safe zone, with distance and helicopter ETA
- villages with **no safe zone within 1.3 km**, flagged for priority attention

A landing zone is **safe** when it is flat, has open land cover, is dry, is clear of buildings
and power lines, is at least **50 x 50 m** and is at least **120 m from water**.

## Spatial methods
| Method | Where |
|---|---|
| Flood simulation from DEM (fill to level) | Offline, QGIS/Python → `flood_extent` |
| Slope analysis | Offline → `candidate_lz.mean_slope` |
| Land-cover reclassification | Offline → `candidate_lz.landcover` |
| Multi-criteria suitability | Offline → `candidate_lz` polygons |
| Buffers (water 120 m, hazards) | Live, PostGIS `ST_Buffer` |
| Overlay / intersection | Live, `ST_Difference`, `ST_Intersects` |
| Nearest facility (KNN) | Live, `<->` with `LATERAL` |

**Design:** heavy raster work is **precomputed offline**. PostGIS answers each slider level
**live** with fast vector queries. Geometry is stored in **EPSG:32736 (UTM 36S)** so all
measurements are in metres, and the app returns **EPSG:4326 GeoJSON** to the map.

## Data sources
| Layer | Source | How it is fetched |
|---|---|---|
| Dowa boundary | geoBoundaries MWI ADM2 (simplified) | filtered to Dowa |
| Villages, roads, rivers, buildings, power lines | OpenStreetMap | Overpass API, clipped to the study area (OSM API fallback) |
| Elevation (DEM) | Copernicus GLO-30 (AWS open data) | windowed read of the study area only |
| Land cover | ESA WorldCover 10 m 2021 (AWS open data) | windowed read of the study area only |

**Study area:** 10 x 8 km centred on Mponela, bbox `33.6909, -13.5657, 33.7833, -13.4937` (W, S, E, N, EPSG:4326).

## Assumptions
All assumptions are set in `config.toml` and can be changed there.
- **Village population.** OSM rarely records population, so we estimate it. Each village gets a
  Voronoi polygon (the area closer to it than to any other village). We count the buildings in
  that polygon and multiply by **4.4 persons per building**. This assumes one building is roughly one
  household, using the average household size from the 2018 Malawi Population and Housing Census.
  *Limitations:* non-residential buildings are counted, compounds with several buildings are
  overcounted, unmapped buildings are missed, and villages at the edge of the study area have cut-off cells.
- **Helicopter staging base:** Kamuzu International Airport, Lilongwe (-13.789, 33.781).
- **Helicopter cruise speed:** 180 km/h, used for ETA.

## Stack
PostgreSQL 17 + PostGIS 3.7 · Python 3.12 · FastAPI + psycopg · React (Vite) + Leaflet · QGIS

## Repository layout
```
data/raw  data/processed  db/  scripts/  backend/  frontend/  qgis/  docs/
```

## Setup
1. Install PostgreSQL 17 with PostGIS, then create the database:
   ```powershell
   psql -U postgres -c "CREATE DATABASE sentinel_shield;"
   psql -U postgres -d sentinel_shield -c "CREATE EXTENSION postgis; CREATE EXTENSION postgis_raster;"
   ```
2. Copy the environment template and set your password:
   ```powershell
   Copy-Item .env.example .env   # then edit DB_PASSWORD
   ```
3. Create the tables:
   ```powershell
   psql -U postgres -d sentinel_shield -f db/schema.sql
   ```
4. Create the Python environment and load the data:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt   # ~85 MB
   .\.venv\Scripts\python.exe scripts\download_data.py   # small clipped downloads into data/raw
   .\.venv\Scripts\python.exe scripts\load_data.py       # reproject to EPSG:32736 and load into PostGIS
   ```
   Both scripts can be re-run. Files that were already downloaded are skipped (use `--force` to download them again), and the loader clears the tables and reloads them.
5. Run the backend and frontend. *(Instructions will be added as these parts are built.)*
