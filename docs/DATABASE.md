# Database design (PostgreSQL 17 + PostGIS 3.7)

## Design principles
1. **One coordinate system for storage: EPSG:32736 (WGS 84 / UTM zone 36S).** Units are metres,
   so `ST_Distance`, `ST_DWithin`, `ST_Area` and `ST_Buffer` give metres and square metres directly.
   We only transform to EPSG:4326 (longitude/latitude) at the last moment, when building GeoJSON for the browser.
2. **Typed geometry columns**, e.g. `geometry(MultiPolygon, 32736)`. The database refuses a wrong
   shape or a wrong SRID, which catches loading mistakes early.
3. **A GIST spatial index on every geometry column.** It stores bounding boxes in an R-tree, so a
   spatial filter first throws away everything whose box can't match, then checks only the few that can.
4. **Precompute the slow raster work, query the fast vector work live.** `flood_extent` and
   `candidate_lz` are produced offline. The question that changes with the slider is answered live by SQL.
5. **Re-runnable scripts:** `CREATE ... IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`, and loaders that `TRUNCATE` and reload in one transaction.

## Tables
| Table | Geometry | Key columns | Role |
|---|---|---|---|
| `district_boundary` | MultiPolygon | name | Dowa District outline (map context) |
| `study_area` | MultiPolygon | name, description | 10 x 8 km analysis window. Everything is clipped to it |
| `rivers` | MultiLineString | name, waterway | Drainage for the flood model, and water context |
| `roads` | MultiLineString | name, highway | Shown on the map, and tested for flood cuts |
| `villages` | Point (+ `footprint` MultiPolygon) | name, population | Settlements. `geom` is the centre (used for distances), `footprint` is the built-up area (used for the flood test) |
| `buildings` | MultiPolygon | building (source: osm / microsoft) | Obstacles (30 m clearance) and the basis of the population estimate |
| `power_lines` | MultiLineString | voltage | Hazards (50 m clearance). OSM has none in this area |
| `flood_extent` | MultiPolygon | **level_m UNIQUE**, area_m2 | One flood polygon per 0.5 m step (0 to 6 m) |
| `candidate_lz` | MultiPolygon (+ `pad` Point) | **code UNIQUE**, area_m2, mean_slope, landcover, score | Zones that pass the static criteria. `pad` is the exact landing spot |
| `staging_base` | Point | name | Kamuzu International Airport, the starting point for ETA |
| `dem`, `landcover` | raster | | The input rasters, stored in PostGIS too (`raster2pgsql`, 100 x 100 tiles) |

**Materialized view `flood_extent_parts`:** each flood polygon cut into pieces of at most 255 vertices
(`ST_Subdivide`), with its own GIST index. A single, detailed flood polygon has one huge bounding box
that overlaps almost everything, so the index can't help. Small pieces have small boxes, so
`ST_Intersects` and `ST_DWithin` only test the pieces that are actually nearby.

## Constraints
- `flood_extent.level_m` is `UNIQUE` and `CHECK (level_m BETWEEN 0 AND 6)`, so there is exactly one polygon per slider level.
- `candidate_lz.code` is `UNIQUE` (LZ-01, LZ-02, ... act as radio call-signs). `area_m2 > 0`.
- `villages.population >= 0`.

## The live query: `sentinel_scenario(level)`
```
flood      = flood_extent_parts WHERE level_m = level
lost LZ    = candidate_lz  WHERE ST_DWithin(zone, flood, 120)        -- within 120 m of water
safe LZ    = all others
stranded   = villages      WHERE ST_Intersects(footprint, flood)     -- spatial intersection
nearest    = LEFT JOIN LATERAL (
               SELECT ... FROM candidate_lz c
               WHERE NOT ST_DWithin(c.geom, flood, 120)              -- safe only
               ORDER BY c.pad <-> village.geom                       -- KNN using the GIST index
               LIMIT 1)
reachable  = distance <= 1300 m
ETA        = ST_Distance(staging_base, pad) / (180 km/h)
roads cut  = ST_Intersection(road, flood)
```
It returns one JSON document (`jsonb_build_object` + `jsonb_agg`), with every geometry in EPSG:4326
via `ST_AsGeoJSON(ST_Transform(geom, 4326))`. FastAPI passes this text straight to the browser.

**Why LATERAL?** A normal subquery can't refer to columns of the row being processed. `LATERAL`
lets the nearest-zone subquery run once per stranded village, using that village's position.

**Why `<->`?** It is PostGIS's distance operator that the GIST index understands. `ORDER BY a <-> b LIMIT 1`
walks the index outwards from the village and stops at the first match. This is the standard
"K nearest neighbours" (KNN) pattern, and it doesn't measure the distance to every zone.

**Why `LEFT JOIN`?** A stranded village with no safe zone at all still appears in the result (with
NULL zone), so we can report it as "no safe zone, send a boat team".

## Performance
See PROGRESS.md for `EXPLAIN ANALYZE` timings per level. The target is under 200 ms per level.
