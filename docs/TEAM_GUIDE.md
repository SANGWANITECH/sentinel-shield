# Team guide: start here

This guide is for team members who have not seen the code yet. It assumes you know programming
and databases but are new to GIS (Geographic Information Systems). It gives you the big picture and
points to the other files in `docs/` for details:

| File | Read it for |
|---|---|
| [METHODS.md](METHODS.md) | Each spatial method explained in more depth |
| [DATABASE.md](DATABASE.md) | Tables, indexes and the live SQL function |
| [CODE_GUIDE.md](CODE_GUIDE.md) | What every file in the repo does |
| [DEMO_SCRIPT.md](DEMO_SCRIPT.md) | The 3-minute live demo, word for word |
| [QA_PREP.md](QA_PREP.md) | 25 likely lecturer questions with answers |
| [SLIDE.md](SLIDE.md) | Content for the presentation slide |
| [../qgis/README.md](../qgis/README.md) | Redoing the analysis by hand in QGIS |
| [../PROGRESS.md](../PROGRESS.md) | Build log: every problem, decision and number |

---

## 1. The project in one paragraph

In January 2024, flash floods on the Kasangadzi River around Mponela in Dowa District, Malawi, cut
off villages. A rescue helicopter needs a safe place to land near them: flat, open ground that is
dry, big enough (at least 50 x 50 m) and away from buildings, power lines and water. As the water
rises, those places disappear. **Sentinel Shield** is a web app with a flood-level slider (0 to 6 m).
For each level it shows the flood, which villages are stranded, which landing zones are still safe,
the nearest safe zone for each stranded village (with distance and flight time), and which villages
have no safe zone within 1.3 km and need a boat team instead. We chose a 10 x 8 km area around
Mponela because the floods there were real and recent, and because the area is small enough to
process quickly and demo live on a laptop.

## 2. How the system was built (in order)

1. **Data download** (`scripts/download_data.py`). We downloaded only our 10 x 8 km box: elevation,
   land cover, roads, rivers, buildings and the district boundary. The internet was very slow, so
   rasters were read as small windows out of cloud files instead of downloading whole tiles.
2. **Loading** (`scripts/load_data.py`). Everything was converted to one coordinate system in metres,
   clipped to the box and loaded into PostgreSQL. OSM had only one village point, so we built 49
   settlements from clusters of buildings and estimated each one's population.
3. **Terrain processing** (`scripts/process_terrain.py`). The slow raster analysis runs once, offline:
   slope, land cover, buffers and suitability produce 40 candidate landing zones, and a flood model
   produces one flood polygon for every 0.5 m from 0 to 6 m.
4. **SQL function** (`db/functions.sql`). `sentinel_scenario(level)` answers "what happens at this
   water level?" inside the database in about 50 ms and returns map-ready GeoJSON.
5. **API** (`backend/app/main.py`). A small FastAPI server that the browser calls. It runs the SQL
   function and passes the result straight through.
6. **Frontend** (`frontend/src/`). A React app with a Leaflet map and a dark "command centre" side
   panel. Moving the slider calls the API and redraws the map.

## 3. Tools and technologies

| Tool | What we used it for | Why we chose it |
|---|---|---|
| PostgreSQL 17 + PostGIS 3.7 | Stores all layers, runs the live spatial queries | PostGIS is the standard spatial database: spatial types, spatial indexes, hundreds of spatial functions |
| Python 3.12 | All offline processing scripts | Best ecosystem for geodata |
| geopandas | Vector data (points, lines, polygons) as tables | Like pandas, but each row has a geometry |
| rasterio | Reading and writing rasters (grids such as the DEM) | Can read just a window of a cloud file |
| shapely | Geometry operations (buffer, union, Voronoi, nearest) | Fast, used by geopandas underneath |
| numpy | Grid maths (slope, masks, HAND) | Rasters are just numpy arrays |
| psycopg 3 + SQLAlchemy + GeoAlchemy2 | Talking to PostgreSQL from Python | Standard drivers; GeoAlchemy2 writes geometries |
| FastAPI (+ uvicorn) | The web API | Very little code, automatic docs at `/docs`, fast |
| React 18 | The user interface | Component-based, state drives the UI |
| Vite 6 | Dev server and production build of the frontend | Fast, simple config, can proxy `/api` to FastAPI |
| Leaflet 1.9 | The interactive map | Lightweight, works offline, full control over layers |
| QGIS | Checking results visually, screenshots, manual reproduction | Free desktop GIS, opens our GeoPackage directly |
| Copernicus GLO-30 DEM | Elevation (30 m grid) for slope, flood model, hillshade | Free, global, good quality |
| ESA WorldCover 2021 | Land cover (10 m grid): crop, grass, trees, water... | Free, global, recent, fine resolution |
| OpenStreetMap (OSM) | Roads, rivers, place names, some buildings | Free, open, the only source of roads/rivers here |
| Microsoft Building Footprints | 14,110 extra buildings detected from satellite images | OSM had only 831 buildings in 80 km² |
| geoBoundaries | Dowa District boundary | Free, clean administrative boundaries |
| Git / GitHub | Version control and submission | Required; every phase is its own commit |

## 4. How it works end to end

The key idea: **slow raster work is done once offline; fast vector questions are answered live.**

```
OFFLINE (run once, minutes)                LIVE (every slider move, ~50 ms)

download_data.py  -> raw files             Browser: React + Leaflet
load_data.py      -> PostGIS tables           |  GET /api/scenario/4.0
process_terrain.py-> landing zones,           v
                     flood polygons        FastAPI (backend/app/main.py)
                                              |  SELECT sentinel_scenario(4.0)
                                              v
                                           PostGIS (db/functions.sql)
                                              |  JSON (GeoJSON layers + totals)
                                              v
                                           back to the browser -> map redraws
```

**Walk-through: the slider moves to 4 m.**
1. React sets the level to 4.0 and calls `GET /api/scenario/4.0` (`frontend/src/App.jsx`). If an
   older request is still running, it is cancelled.
2. FastAPI checks that 4.0 is a valid level and runs `SELECT sentinel_scenario(4.0, 120, 1300, 180)`
   (clearance 120 m, reach 1300 m, speed 180 km/h, all from `config.toml`).
3. Inside PostGIS, the function:
   - picks the 4 m flood polygon (pre-cut into small pieces for fast indexing);
   - marks each of the 40 landing zones **lost** if it is within 120 m of the flood (`ST_DWithin`),
     otherwise **safe**;
   - finds **stranded** villages: those whose built-up area touches the flood (`ST_Intersects`);
   - for each stranded village, finds the **nearest safe** landing zone with a KNN search
     (`LEFT JOIN LATERAL ... ORDER BY pad <-> village LIMIT 1`), marks it reachable if within 1300 m,
     and computes ETA = distance from Kamuzu International Airport to the zone / 180 km/h;
   - finds **road cuts** (`ST_Intersection` of roads and flood, ignoring the bridge over the normal river);
   - converts every geometry to longitude/latitude (`ST_Transform(..., 4326)`) and builds one JSON document.
4. FastAPI returns that JSON unchanged, with the query time in an `X-Query-Ms` header.
5. Leaflet redraws: the flood fades in, lost zones turn dashed grey, stranded villages turn amber
   (reachable) or red (no safe zone), dashed green lines link villages to zones, and the counters update.
   At 4 m you should see 15,413 people exposed, 9 stranded, 5 reachable, 4 with no safe zone.

## 5. The spatial methods in brief

Details and reasoning are in [METHODS.md](METHODS.md).

| Method | What it does | Where in the code |
|---|---|---|
| **Slope** | Steepness of each 30 m DEM cell in degrees; landing needs at most 7 degrees. | `compute_slope()` in `scripts/process_terrain.py` |
| **Reclassification** | Turns WorldCover's 11 classes into open (grass 30, crop 40, bare 60) or not (trees, shrubs, built-up, water, wetland). | `candidate_landing_zones()` in `process_terrain.py` |
| **Buffers** | Zones of fixed distance: 30 m round buildings and 50 m round power lines are excluded offline; 120 m from the flood is checked live. | `process_terrain.py`; `ST_DWithin` in `db/functions.sql` |
| **Multi-criteria suitability** | A cell is suitable only if every rule is true (flat AND open AND clear of buildings AND power lines). Then a -25 m buffer test keeps only areas where a 50 x 50 m square fits. | `candidate_landing_zones()` |
| **HAND flood model** | Height Above Nearest Drainage: each cell's height above its nearest river cell. A level-L flood covers cells with HAND <= L that connect to the river. | `flood_levels()` in `process_terrain.py` |
| **Voronoi population** | Each settlement gets the area closer to it than any other; buildings inside x 4.4 people. | `estimate_population()` in `scripts/load_data.py` |
| **Spatial intersection** | Which villages and roads the flood touches. | `ST_Intersects` / `ST_Intersection` in `db/functions.sql` |
| **KNN nearest landing zone** | For each stranded village, walks the spatial index outwards to the closest safe zone. | `LATERAL ... <->` in `db/functions.sql` |
| **Coordinate systems** | Stored in **EPSG:32736** (UTM zone 36S, units = metres) so distances and areas are correct; sent to the browser in **EPSG:4326** (longitude/latitude), which web maps expect. | `db/schema.sql`; `ST_Transform` in `functions.sql` |
| **GIST indexes** | Spatial indexes (R-trees of bounding boxes) on every geometry column, so spatial filters skip far-away rows. | `db/schema.sql`, `db/functions.sql` |

## 6. How to run it

All commands are run in PowerShell from the repo folder.

**Start** (PostgreSQL must be running; it normally starts with Windows):
```powershell
.\scripts\start.ps1 -Preview
```
This opens two windows (API on port 8000, UI on port 4173) and the browser at http://localhost:4173.
Without `-Preview` it uses the development server on port 5173 instead.

**Stop:** close the two windows titled "Sentinel Shield API" and "Sentinel Shield UI".

**On another laptop** (for example the demo machine):
1. Install PostgreSQL 17 with PostGIS, Python 3.12, Node.js and Git, then clone the repo.
2. `Copy-Item .env.example .env` and put that machine's PostgreSQL password in it.
3. `.\scripts\restore_db.ps1` rebuilds the whole database from `db/sentinel_shield.dump` (no internet needed).
4. Install packages: `python -m venv .venv`, `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`,
   then `cd frontend; npm install; cd ..`
5. `.\scripts\start.ps1 -Preview`

Full instructions, including rebuilding everything from raw data, are in the main [README](../README.md).

## 7. How to test it

**Automated tests** (7 tests against the real database):
```powershell
cd backend; ..\.venv\Scripts\python.exe -m pytest -q; cd ..
```
Expected: `7 passed`.

**Check the API by hand** (with the app running):
```powershell
Invoke-RestMethod http://localhost:8000/api/health
(Invoke-RestMethod http://localhost:8000/api/meta).criteria
(Invoke-RestMethod http://localhost:8000/api/scenario/4).totals
```
Interactive API docs: http://localhost:8000/docs

**Show the numbers come from the database** (same totals as the screen at 4 m):
```powershell
psql -h localhost -U postgres -d sentinel_shield -c "SELECT sentinel_scenario(4)->'totals';"
```
Every level at once:
```powershell
psql -h localhost -U postgres -d sentinel_shield -c "SELECT level_m, sentinel_scenario(level_m)->'totals' FROM flood_extent ORDER BY level_m;"
```
Query speed: add `EXPLAIN ANALYZE` in front of `SELECT sentinel_scenario(4);` and read "Execution Time".

**Check the layers in QGIS:** open `data/processed/sentinel_shield.gpkg` (drag it into QGIS and select
all layers). Filter `flood_extent` to `"level_m" = 4`, and check that green landing zones near the
flood are the ones the app shows as lost. [../qgis/README.md](../qgis/README.md) explains how to redo
slope, reclassification and suitability by hand.

## 8. Proving it live in the presentation

**Before the session**
- [ ] Laptop charged, PostgreSQL running, `.\scripts\start.ps1 -Preview` shows "Backend OK".
- [ ] Browser full screen at http://localhost:4173, slider at 0 (press Reset).
- [ ] A second PowerShell window open with the psql command from section 7 ready to run.
- [ ] Test the play button once, then Reset.

**Moves that show it is database-driven**
1. **Query time on screen:** bottom of the side panel shows "PostGIS xx ms", which changes every level.
2. **`?level=` URL:** type `http://localhost:4173/?level=4` and the app opens at 4 m straight away.
3. **Live psql query:** run `SELECT sentinel_scenario(4)->'totals';` in front of the audience and point
   out that the numbers equal the counters on screen.
4. **Optional:** stop the API window and the "PostGIS live" badge turns red; restart it and it recovers.

The full spoken script is in [DEMO_SCRIPT.md](DEMO_SCRIPT.md).

## 9. Known limitations and honest answers

| Limitation | Honest answer |
|---|---|
| The river has no name in OSM | "OSM has no feature called Kasangadzi. We treat the unnamed river crossing the M1 just north of Mponela as the Kasangadzi, based on reports of the 2024 floods." |
| Most settlement names are "Settlement NN" | "OSM had only one village point here, so we derived 49 settlements from building clusters and named them from nearby OSM features where possible." |
| The area is a flat plateau | "96% of the area is under 7 degrees, so slope removes little; land cover, buildings and the flood decide most. That is true of the terrain, not a bug." |
| No power lines in OSM | "The 50 m power-line rule is implemented, but OSM maps no power lines in this area, so it excludes nothing here." |
| Simplified flood model | "HAND is a terrain-based scenario model, not a hydraulic model. It ignores rainfall, flow and timing. A hydraulic model needs gauge and channel data we did not have." |
| Estimated population | "Buildings in each Voronoi cell times 4.4 people (2018 Census household size). Non-residential and unmapped buildings add error." |

More answers in [QA_PREP.md](QA_PREP.md), section F.

## 10. Who should study what

Everyone should read sections 1 to 4 of this guide and run the app once. Then split the deeper topics
so every question has an owner (adjust to your team size):

| Member | Topic | Study these |
|---|---|---|
| 1 | Problem, study area, data sources, demo | Section 1 and 3 here, [DEMO_SCRIPT.md](DEMO_SCRIPT.md), [SLIDE.md](SLIDE.md), `scripts/download_data.py` |
| 2 | Raster methods: slope, reclassification, buffers, suitability | [METHODS.md](METHODS.md) sections 2 to 6, `scripts/process_terrain.py`, [../qgis/README.md](../qgis/README.md) |
| 3 | Flood model and population | [METHODS.md](METHODS.md) sections 7 and 8, `flood_levels()`, `scripts/load_data.py` |
| 4 | Database design and the live SQL | [DATABASE.md](DATABASE.md), `db/schema.sql`, `db/functions.sql`, KNN and GIST questions |
| 5 | API, frontend and testing | [CODE_GUIDE.md](CODE_GUIDE.md), `backend/`, `frontend/src/`, section 7 here |

Before the presentation, each member explains their topic to the others in 5 minutes, and everyone
reads [QA_PREP.md](QA_PREP.md) once.
