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
4. Load data, then run the backend and frontend. *(Instructions will be added as these parts are built.)*
