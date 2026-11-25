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
