# Code guide: what every file does

Plain-language notes so every team member can explain any file in the Q&A.

## How the pieces fit together
```
 OFFLINE (run once, minutes)                        LIVE (every slider move, milliseconds)
 ---------------------------                        --------------------------------------
 download_data.py  -> data/raw                      Browser (React + Leaflet)
 load_data.py      -> PostGIS tables                  |  GET /api/scenario/3.5
 process_terrain.py-> candidate_lz, flood_extent      v
                                                    FastAPI (backend/app/main.py)
                                                      |  SELECT sentinel_scenario(3.5)
                                                      v
                                                    PostGIS (db/functions.sql)
                                                      ST_DWithin, ST_Intersects, KNN <->, LATERAL
```
**Why split it like this?** Raster analysis such as slope and flood modelling is slow (seconds to minutes), but its
results don't change between slider moves, so we compute it once. Choosing which zones are still
safe at a given water level is a fast vector question, so PostGIS answers it live in milliseconds.

---

## Configuration
| File | What it does |
|---|---|
| `config.toml` | Every assumption and threshold in one place: study area box, slope limit, buffers, helicopter speed, persons per building. Change a value here and re-run the scripts. |
| `.env` / `.env.example` | Database login. `.env` holds the real password and is never committed. `.env.example` is the template. |
| `requirements.txt` / `requirements.lock` | Python packages. The `.lock` file pins exact versions so `scripts/fetch_wheels.py` can download them on a slow line. |

## Database (`db/`)
| File | What it does |
|---|---|
| `schema.sql` | Creates the 10 tables. All geometry is stored in **EPSG:32736 (UTM 36S)** so lengths and areas are in metres, and every geometry column has a **GIST spatial index**. Safe to re-run. |
| `functions.sql` | The live analysis. `sentinel_scenario(level)` returns everything for one water level as GeoJSON. `sentinel_base_layers()` returns the static map layers. `flood_extent_parts` is a materialized view of the flood polygons cut into small pieces (ST_Subdivide) so the index is effective. |

## Scripts (`scripts/`)
| File | What it does |
|---|---|
| `common.py` | Shared helpers: folder paths, reading `config.toml` and `.env`. |
| `fetch_wheels.py` | Slow-internet installer: downloads each pinned Python wheel with resume (via curl), then pip installs offline. |
| `download_data.py` | Downloads only the study area: Dowa boundary (geoBoundaries), OSM villages/roads/rivers/buildings/power lines (Overpass, OSM API fallback), DEM and land cover by *windowed reads* of cloud-optimised GeoTIFFs. Skips files it already has. |
| `load_data.py` | Reprojects everything to UTM 36S, clips it to the study area, estimates village population (Voronoi cells x buildings x 4.4), and loads it into PostGIS. Clears the tables first, so it can be re-run. Loads the DEM and land cover as PostGIS rasters too. |
| `process_terrain.py` | The raster analysis: slope, land-cover reclassification, suitability, landing zones, the HAND flood model, the hillshade basemap and the GeoPackage export. |
| `start.ps1` | Starts the backend and frontend and opens the browser. |

## Backend (`backend/`)
| File | What it does |
|---|---|
| `app/settings.py` | Reads `.env` and `config.toml`. |
| `app/main.py` | FastAPI app with 4 endpoints: `/api/health`, `/api/meta`, `/api/scenario/{level}`, `/api/base-layers`. It uses a small connection pool (psycopg_pool) so each request reuses an open DB connection. It passes the JSON built by PostGIS straight through, so Python never rebuilds it. |
| `tests/test_api.py` | pytest tests against the real database: totals add up, flood grows with level, coordinates are lon/lat, unknown level gives 404. |

## Frontend (`frontend/`)
| File | What it does |
|---|---|
| `src/main.jsx` | Entry point. Imports the bundled fonts and the Leaflet CSS, so nothing loads from the internet. |
| `src/App.jsx` | Holds the state (current level, scenario data, layer toggles) and the play timer (+0.5 m every 700 ms). Each new level triggers one API call, and a newer call cancels an older one (AbortController). |
| `src/MapView.jsx` | Leaflet map. Draws the hillshade basemap and layers, and restyles markers when the level changes (CSS transitions give the smooth fades). Each layer type has its own *pane*, so a toggle just hides that pane. |
| `src/Panel.jsx` | Side panel: animated counters, slider, play/reset, rescue assignments sorted by population, layer toggles, criteria chips. |
| `src/styles.css` | The dark command-centre theme. |
| `public/basemap/hillshade.png` | Offline basemap made from the DEM by `process_terrain.py`. |
| `vite.config.js` | Dev server on port 5173. Forwards `/api` to FastAPI on port 8000. |
