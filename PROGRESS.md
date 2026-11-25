# PROGRESS

## Morning summary (overnight run, 25 Nov 2026)

**All six phases (A to F) are built, tested, committed and pushed.**

### How to run the app
```powershell
cd C:\Users\pc.DESKTOP-5HLK5E7\Documents\sentinel-shield
.\scripts\start.ps1 -Preview        # opens http://localhost:4173 (API on :8000)
```
Two PowerShell windows open (API and UI). Close them to stop. Then press **Simulate rising water**.
Tip: `http://localhost:4173/?level=4` opens straight at 4 m.
Tests: `cd backend; ..\.venv\Scripts\python.exe -m pytest -q` (7 pass).

### What works
- Full pipeline: download (clipped) -> load (EPSG:32736) -> terrain processing -> live SQL -> API -> UI.
- 49 settlements (~65,700 people), 14,941 buildings, 40 landing zones, 13 flood levels (0 to 6 m, 1.4 to 6.8 km²).
- Live query 40 to 84 ms per level (target < 200 ms). The KNN index is used (`Order By: pad <-> geom`).
- At 6 m: 16,434 people exposed, 11 settlements stranded, 7 reachable by helicopter, 4 with no safe zone, 31 roads cut, 5 zones lost.
- The UI matches the mockup brief: dark command centre, offline hillshade, animated counters, play/reset, rescue
  list, layer toggles, criteria chips, scale bar, north arrow, water level readout. No console errors. Works offline.
- Team docs in `docs/`, manual QGIS steps in `qgis/README.md`, GeoPackage for QGIS in `data/processed/`.

### What does not work, or is weaker than hoped (please read)
- **Village names:** OSM has almost no names here, so most settlements are "Settlement NN" (plus Mponela,
  Mponela Rural, St. Chavara). If the team knows real village names, edit the `villages.name` column
  (`UPDATE villages SET name='...' WHERE id=..`) and re-dump. The app shows names straight from the DB.
- **Kasangadzi River is not named in OSM.** We assume the unnamed river crossing the M1 north of Mponela is it. Please confirm.
- **No power lines in OSM** for this area, so that criterion is implemented but excludes nothing.
- **Flood model is HAND (terrain-based), not hydraulic.** This is stated clearly in docs/METHODS.md and QA_PREP.md.
- `restore_db.ps1` was not executed (see Phase F). The headless screenshots can't capture animations, so
  please check the motion (flood fade, pulsing ring, counters) once in a real browser.
- I couldn't turn the internet off to test offline mode. The bundle audit shows no runtime external requests.

### Every decision I made (details in the phase log below)
1. No scipy: nearest drainage via shapely STRtree (exact, same as distance_transform_edt indices). Saved 45 MB.
2. No @vitejs/plugin-react (Babel): Vite's esbuild compiles JSX. Plain Leaflet instead of react-leaflet.
3. pip replaced by `fetch_wheels.py` + `requirements.lock` (curl + resume, offline install), because pip and Python TLS kept timing out.
4. All HTTP in download_data.py goes through curl with retries and resume.
5. Bbox kept (Mponela and the river both inside). The unnamed OSM river is treated as the Kasangadzi.
6. Settlements derived from building clusters (100 m link, >= 40 buildings, town split into 1 km sectors) -> 49.
7. Microsoft building footprints added (2 x 30 MB tiles): the one exception to minimal downloads, needed for population.
8. Village `footprint` polygon: stranded = flood touches the built-up area (a single point was too strict).
9. Landing zones: suitable area tiled into 250 m squares, 50 x 50 m test = mitre-joined -25 m buffer,
   score 0.6 flatness + 0.4 size, greedy selection with the largest spacing that yields 40 (1,200 m).
10. Landing spot `pad` = pole of inaccessibility. KNN, distances, ETA and markers use it.
11. Level 0.0 = normal river channel: keeps the 120 m clearance at the bottom of the slider. No stranding or road cuts at 0.
12. Road cut = > 20 m flooded outside the normal channel (so bridges over the river don't count at every level).
13. Safe LZ test uses the whole zone polygon with ST_DWithin 120 m (conservative, as specified).
14. Materialized view `flood_extent_parts` (ST_Subdivide) for fast index lookups.
15. Hillshade: DEM read at 3x (bilinear), 3x vertical exaggeration, dark tones, own Leaflet pane.
16. `?level=` URL parameter. Counters respect "reduce motion". `start.ps1 -NoBrowser` switch.
17. Committed `data/processed/sentinel_shield.gpkg` (5.6 MB) and `db/sentinel_shield.dump` (2.3 MB) so teammates and the demo laptop need no internet.

### Suggested next steps for the team
- Confirm the river and add real village names. Take QGIS screenshots for the slide (qgis/README.md).
- Rehearse with docs/DEMO_SCRIPT.md. Each member reads docs/QA_PREP.md.
- Optional: test `restore_db.ps1` on the demo laptop.

## Log

### Overnight run started 2026-11-25 ~03:30
Decisions taken up front (to keep downloads small on a ~30 KB/s line):
- **No scipy.** "Nearest drainage" for HAND uses shapely `STRtree.query_nearest` over the river-cell
  centres. This gives the same exact Euclidean nearest cell as `scipy.ndimage.distance_transform_edt(return_indices=True)`.
  Flood connectivity is tested by polygonising the HAND mask and keeping polygons that touch the river.
  This saves a ~45 MB download.
- **No @vitejs/plugin-react (Babel).** Vite's built-in esbuild compiles JSX (`esbuild.jsx = "automatic"`).
  The only cost is losing hot reload of React state, which doesn't matter for the demo.
- **Plain Leaflet instead of react-leaflet:** one fewer dependency and full control over the map layers.

### Phase A: data download and load (DONE)
**Problems and fixes**
- `pip install` kept failing with "from versions: none" for numpy. pip first downloads each package's
  full index page, and numpy's is several MB, so it timed out on this line. Python's own TLS handshakes
  also stalled (>120 s) while curl's took seconds.
  **Fix:** `scripts/fetch_wheels.py` reads PyPI's small per-version JSON, downloads exactly one wheel per
  pinned package (`requirements.lock`, 46 packages, 88 MB) with **curl + resume**, then runs `pip install --no-index`.
- For the same reason, `download_data.py` now does all HTTP through curl (with retries and resume for big files).
- Overpass was overloaded (502s and timeouts). The mirror rotation with backoff got every layer in the end.
  The OSM API fallback was never needed.

**Decisions**
- **Kasangadzi River:** OSM has no river by that name. The study area has 9 *unnamed* `waterway=river` ways
  (about 15 km) crossing the M1 just north of Mponela. We treat this river as the Kasangadzi. Both it and Mponela are inside the bbox,
  so **the bbox was not changed** (33.6909, -13.5657, 33.7833, -13.4937).
- **Villages:** OSM has only **1** place node here (Mponela town) and 3 named features. We **derive
  settlements from building clusters**: buildings within 100 m of each other form a cluster, clusters with at least 40 buildings
  count as settlements, and the town is split into 1 km sectors. Each is named after the nearest named OSM feature within 600 m,
  otherwise "Settlement NN". Settings are in `config.toml [settlements]`. The first try (60 m / 15 buildings) gave 138 = too many.
  Final: **49 settlements** (target 15 to 60), covering 92% of buildings.
- **Buildings:** OSM has only 831 in 80 km². We added **Microsoft Global ML Building Footprints**
  (14,110 extra, non-overlapping). This needed two 30 MB tiles, because the dataset has no clipped download.
  This is the one exception to "minimal downloads", and it was needed for meaningful population and villages.
  The files are cached in `data/raw/_cache` so they never download again.
- **Village footprint:** villages now have a `footprint` polygon (merged 50 m buffers around their buildings).
  A village is "stranded" when the flood touches its built-up area. A single centre point was too strict.
- **Power lines:** OSM has **none** in the study area. The 50 m criterion is implemented but excludes nothing.
- Population estimate: Voronoi cell x buildings x 4.4. Total about **65,700 people** in the study area.

**Row counts**
| table | rows |
|---|---|
| district_boundary | 1 |
| study_area | 1 |
| rivers | 9 |
| roads | 375 |
| villages | 49 |
| buildings | 14,941 (831 OSM + 14,110 Microsoft) |
| power_lines | 0 |
| staging_base | 1 |
| dem (raster, 100x100 tiles) | 12 |
| landcover (raster, 100x100 tiles) | 107 |

**data/raw file sizes**
| file | size |
|---|---|
| dem_cop30.tif (windowed read) | 319 KB |
| worldcover_2021.tif (windowed read) | 77 KB |
| dowa_boundary.geojson | 53 KB |
| osm_roads.geojson | 404 KB |
| osm_buildings.geojson | 207 KB |
| osm_rivers.geojson | 15 KB |
| osm_places / named_features / power_lines | < 1 KB each |
| ms_buildings.geojson (clipped) | 4.8 MB |
| _cache: Microsoft tiles + index | 30 + 31 + 7 MB |

### Phase B: terrain processing (DONE)
`scripts/process_terrain.py` (about 30 s run time).
- **Slope** from the 30 m UTM DEM (numpy gradient). Flat (<= 7 degrees): 96% of the study area. The plateau is very flat.
- **Reclassification:** open ground = WorldCover 30/40/60 makes up 83% of the area (mostly cropland).
- **Suitability** = flat AND open AND >= 30 m from buildings AND >= 50 m from power lines (none present): 70% of cells.
- **Candidate LZs:** the suitable area is one huge connected region, so it is **cut into 250 m tiles**. The 50 x 50 m
  test is a negative 25 m buffer with mitre joins (exact for pixel-edged polygons). 1,162 tile pieces pass.
  **Selection method:** score = 0.6 x flatness + 0.4 x size, then greedy picking by score with a minimum spacing
  between landing spots (the largest of 1200/1000/800/600/400/250 m that still gives 40). Result: **40 zones at >= 1,200 m spacing**.
  All cropland, slope 0.5 to 3.4 degrees, 2.3 to 6.25 ha, landing spot >= 90 m from the nearest building.
  Village to nearest LZ: 682 m on average, 1,472 m at most.
- **Landing spot (`pad`)** = pole of inaccessibility of the 25 m-eroded zone. Used for KNN, distances and markers.
- **HAND flood model:** drainage = rasterised OSM river lines (616 cells). Nearest drainage cell via shapely STRtree
  (no scipy). Flood = HAND <= level AND connected to the river (polygons touching river lines), then closed by 15 m and simplified by 5 m.
  13 levels (0 to 6 m). Area grows steadily from **1.38 km² (0 m = normal channel) to 6.83 km² (6 m)**.
  Documented as a simplified terrain model, not a hydraulic model, in docs/METHODS.md.
- **Hillshade basemap:** DEM read at 3x (bilinear) and shaded from the NW, with 3x vertical exaggeration and dark tones,
  saved as `frontend/public/basemap/hillshade.png` (1098x876) + `hillshade.json` bounds.
- **GeoPackage** `data/processed/sentinel_shield.gpkg` (5.6 MB, committed) with every layer, including `candidate_lz_pads` and `village_voronoi`.
- `qgis/README.md`: step-by-step manual QGIS reproduction (slope, reclass, buffers, raster calculator, polygonise, -25 m buffer, HAND).
- Decision: **level 0.0 is stored** as the normal channel, so landing zones keep their 120 m clearance from the river at the
  bottom of the slider. In the live query nobody is stranded and no road is "cut" at level 0.

### Phase C: database logic (DONE)
`db/functions.sql`: `sentinel_scenario(level, clearance=120, reach=1300, speed=180)` returns one jsonb document
(flood, safe_lz, lost_lz, stranded with nearest LZ / distance / ETA, links, roads_cut, totals) in EPSG:4326.
`sentinel_base_layers()` returns the static layers. Materialized view `flood_extent_parts` (ST_Subdivide, 255 vertices) is refreshed by process_terrain.py.

**Decisions**
- "Stranded" uses the village **footprint** (`ST_Intersects(footprint, flood)`). The nearest-LZ distance is measured from the village centre point.
- Safe LZ = the zone polygon is NOT within 120 m of the flood (`ST_DWithin`, as specified). This is conservative:
  if any part of a zone is within 120 m, the whole zone is lost.
- Level 0 = baseline: nobody is stranded and no road is cut. Road cuts ignore the span over the normal channel (bridges)
  and need > 20 m flooded outside it.
- ETA = distance KIA -> landing spot / 180 km/h (the zones are 25 to 34 km from KIA, so ETAs are 8.3 to 11.3 min).

**Results per level** (stranded / reachable / no safe zone / roads cut / safe LZ):
0.0: 0/0/0/0/37 · 0.5: 5/2/3/5/37 · 1.0: 7/4/3/8/37 · 2.0: 8/5/3/13/37 · 3.0: 8/4/4/15/36 · 4.0: 9/5/4/23/36 · 5.0: 10/6/4/26/36 · 6.0: 11/7/4/31/35

**Benchmark** (`EXPLAIN ANALYZE SELECT sentinel_scenario(level)`, average of 3 runs, local PostgreSQL 17):
| level | ms | level | ms | level | ms |
|---|---|---|---|---|---|
| 0.0 | 40 | 2.0 | 60 | 4.5 | 67 |
| 0.5 | 54 | 2.5 | 60 | 5.0 | 70 |
| 1.0 | 61 | 3.0 | 61 | 5.5 | 69 |
| 1.5 | 59 | 3.5 | 63 | 6.0 | 84 |
| | | 4.0 | 67 | | |
**Every level is under 100 ms (target 200 ms).** The planner uses the KNN index directly:
`Index Scan using candidate_lz_pad_gix ... Order By: (pad <-> v.geom)`, plus `flood_extent_parts_geom_gix` for the
ST_DWithin anti-join. The nearest-LZ step for all 49 villages runs in **3 ms**. Most of the remaining time goes into building the GeoJSON.

### Phase D: backend (DONE)
FastAPI + psycopg 3 with a small connection pool (`psycopg_pool`). Run it with
`.venv\Scripts\python -m uvicorn app.main:app --app-dir backend --port 8000` (or scripts/start.ps1).
- `GET /api/health`: PostGIS version and row counts (503 if the DB is down).
- `GET /api/meta`: bbox, levels, staging base, assumptions and human-readable criteria chips (from config.toml).
- `GET /api/scenario/{level}`: snaps to 0.5 m and returns 404 for unknown levels. Passes the PostGIS JSON straight through,
  with an `X-Query-Ms` header (about 45 ms, 46 KB at 3.5 m).
- `GET /api/base-layers`: district, study area, rivers, roads, power lines, villages, staging base, simplified (135 KB, cached).
- CORS for localhost:5173 (dev) and :4173 (preview). The Vite dev server also proxies /api, so the browser uses one origin.
- **7 pytest tests pass** (`cd backend; ..\.venv\Scripts\python -m pytest -q`): health, meta, scenario shape and totals
  consistency, flood grows with level, WGS84 coordinates, 404 for unknown level, base layers.

### Phase E: frontend (DONE)
React 18 + Vite 6 + plain Leaflet 1.9. Fonts come from @fontsource (Barlow Condensed, IBM Plex Sans/Mono, latin subset), bundled for offline use.
- Header: shield logo, title, subtitle, badges (district/river, SIMULATION, PostGIS live; the badge turns red if the API fails).
- Map: offline hillshade basemap (OSM tiles optional, off by default, dark-filtered), rivers, roads (styled by class),
  power lines, flood (translucent blue, fades between levels), villages (grey / amber stranded+reachable /
  red with pulsing ring = no safe zone), LZ markers "H" (green; dashed grey when lost, with CSS transition),
  dashed green rescue links, red flooded road segments + "ROAD CUT" labels for main roads (spaced >= 450 m apart),
  metric scale bar, north arrow, water level readout (+ km² flooded), legend.
- Side panel: 4 animated counters, slider 0 to 6 m, "Simulate rising water" (+0.5 m every 700 ms) / Pause, Reset,
  rescue assignments sorted by population ("V26 Settlement 25 → LZ-29 · 0.7 km · ETA 10 min" or
  "No safe zone in reach · send boat team"), layer toggles, criteria chips from /api/meta, PostGIS query time.
- `?level=3.5` in the URL opens at that level (used for screenshots and handy in the demo).
- Each slider change makes a live API call. AbortController cancels stale requests while the animation plays.

**Bugs found with headless Edge screenshots, and fixed**
1. The flood and rivers were hidden under the hillshade (Leaflet's imageOverlay sits in overlayPane z=400). The hillshade now has its own pane (z=150).
2. The flood fade-in used requestAnimationFrame, which is paused in background tabs, so the flood could stay invisible. It now forces a style flush and removes the class directly.
3. The rescue list collapsed to about 2 rows when the panel overflowed. Blocks no longer shrink, and the list is >= 260 px tall.
- Counters skip the count-up animation when the OS asks for reduced motion (accessibility).
- Checked: no console errors. Totals on screen match SQL (6 m: 16,434 people, 11 stranded, 7 reachable, 4 no LZ).
- Production bundle: 309 KB JS (95 KB gzip). The only external URL actually requested is the optional OSM tile layer.

### Phase F: demo-ready (DONE)
- `scripts/start.ps1 [-Preview] [-NoBrowser]`: checks the venv, node_modules, .env and the PostgreSQL service, starts uvicorn,
  waits for /api/health, starts Vite (dev, or build + preview) in its own window and opens the browser.
  **Tested** with `-Preview -NoBrowser`: page, API proxy, scenario and hillshade all served, screenshot correct.
  The test windows were closed afterwards.
- **Offline check:** I did not turn the network off (that would be a system setting change). Instead I audited the
  production bundle: no runtime requests except the optional OSM tile layer (off by default). Fonts, Leaflet CSS and the basemap are all local.
- `db/sentinel_shield.dump` (2.3 MB, `pg_dump -Fc`) + `scripts/restore_db.ps1`: rebuild the database on another laptop
  in seconds, no internet needed. The restore skips the EXTENSION entries (the script enables PostGIS itself).
  **Not executed here**: testing it would need a second database or overwriting the live one, and the rules allow only sentinel_shield.
  The dump's contents were checked with `pg_restore -l`.
- docs: `DEMO_SCRIPT.md` (3 min), `SLIDE.md`, `QA_PREP.md` (25 Q&A), `METHODS.md`, `DATABASE.md`, `CODE_GUIDE.md`.
- README rewritten (run instructions for 3 situations, architecture diagram, methods, data, assumptions).
