# PROGRESS

<!-- MORNING SUMMARY goes here at the end of the overnight run -->

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
- ETA = distance KIA -> landing spot / 180 km/h (KIA is about 30 km away, so ETAs are about 9 to 11 min).

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
