# Spatial methods, explained simply

Each method below says **what** it does, **how** we did it and **why**.
All analysis is in **UTM zone 36S (EPSG:32736)**, a projected coordinate system in metres. That makes
"120 m from water" or "50 x 50 m" literal measurements. Degrees of latitude and longitude aren't
the same length everywhere, so they can't be used to measure distance directly.

## 1. Study area and data
- **Study area:** a 10 x 8 km box centred on Mponela, Dowa District, covering the Kasangadzi River
  where the January 2024 flash floods happened.
- **Elevation:** Copernicus GLO-30 DEM (30 m cells). We read only our box from the cloud file
  (a *windowed read* of a Cloud-Optimised GeoTIFF), not the whole 1 x 1 degree tile.
- **Land cover:** ESA WorldCover 2021 (10 m cells), also read as a window.
- **Vectors:** OpenStreetMap villages, roads, rivers, buildings and power lines. Dowa boundary from geoBoundaries.

## 2. Slope analysis
**What:** how steep the ground is, in degrees.
**How:** for each DEM cell, the change in height to the east-west and north-south neighbours
(`numpy.gradient`, cell size 30 m), then `slope = arctan(sqrt(dz/dx^2 + dz/dy^2))`.
**Why:** a helicopter needs nearly flat ground. We use a limit of **7 degrees**. The actual limit
depends on the aircraft, and 7 degrees is a common conservative figure for unprepared sites.

## 3. Land-cover reclassification
**What:** turn 11 WorldCover classes into two: *open ground* (1) or *not suitable* (0).
**How:** grassland (30), cropland (40) and bare / sparse vegetation (60) become 1. Trees (10),
shrubland (20), built-up (50), water (80), wetland (90) and the rest become 0.
**Why:** rotor blades need clearance from trees, built-up areas have obstacles, and water or wetland can't take the weight.

## 4. Buffers
**What:** zones of a fixed distance around features.
**How:** buildings buffered by **30 m**, power lines by **50 m** (both excluded). At run time,
each landing zone must be **120 m** from the flood water (`ST_DWithin` in PostGIS).
**Why:** rotor downwash and obstacle clearance near buildings, wire-strike risk near power lines,
and a safety margin from rising water and soft banks.

## 5. Multi-criteria suitability (Boolean overlay)
**What:** combine all the criteria. A cell is suitable only if **every** criterion is true.
**How:** `suitable = (slope <= 7) AND open_ground AND NOT near_building AND NOT near_power_line`,
computed on the 10 m land-cover grid (the 30 m slope is resampled bilinearly).
**Why:** each criterion is a hard safety rule, so a Boolean AND fits better than a weighted score.
A weighted score is only used later, to *rank* zones that already pass every rule.

## 6. From suitable cells to landing zones
1. **Vectorise**: suitable cells become polygons (`rasterio.features.shapes`).
2. **Minimum size**: we shrink every polygon by 25 m (a negative buffer with square corners). If anything
   is left, a **50 x 50 m** square fits inside. The test is exact for pixel-shaped polygons, because their
   edges run north-south and east-west.
3. **Tile** large open areas into 250 m squares, so each landing zone is one site rather than a whole plain.
4. **Landing spot**: the point inside each zone furthest from its edges (the "pole of inaccessibility"). Distances and map markers use this point.
5. **Rank and spread**: score = 60% flatness + 40% size. We then pick the ~40 best zones **greedily** with a
   minimum spacing between them: the largest spacing (from 1200 m down to 250 m) that still gives 40 zones.
   This spreads zones across the area instead of clustering them on the single best field.

## 7. Flood simulation: HAND (Height Above Nearest Drainage)
> **Important:** this is a **simplified, terrain-based flood model, not a hydraulic model**. It
> answers "if the water rose X metres above the river, which connected low ground would be
> underwater?" It ignores rainfall, flow speed, river discharge, channel shape, bridges, culverts and
> timing. It is a fair *scenario* tool for planning, not a forecast. Hydraulic models
> such as HEC-RAS need river cross-sections and flow data that we don't have.

**How:**
1. Rasterise the river and stream lines onto the DEM grid. These cells are the "drainage".
2. For every cell, find the **nearest drainage cell** (exact straight-line nearest neighbour, using
   a spatial index tree, which gives the same result as SciPy's distance transform).
3. **HAND = cell elevation - elevation of that nearest drainage cell.**
4. For each water level L (0 to 6 m in 0.5 m steps): flooded = cells with HAND <= L **that are
   connected to the river**. We polygonise the mask and keep only polygons touching a river line,
   so isolated hollows don't count.
5. Smooth the pixel edges (buffer out 15 m, then back in) and simplify by 5 m. Load one polygon per level into `flood_extent`.

**Limitations:** Copernicus GLO-30 is a *surface* model, so trees and buildings raise the
surface a little. A 30 m grid can't see small channels or levees. Every connected cell floods at
once (no timing).

## 8. Village population estimate
OSM rarely records population. Each village gets a **Voronoi polygon** (all the land closer to that
village than to any other). We count the OSM buildings inside and multiply by **4.4 people per
building**, the average household size in the 2018 Malawi Census (assuming about one household per
building). It's an estimate: non-residential buildings and unmapped buildings add error.

## 9. Live queries in PostGIS (each slider move)
| Question | PostGIS method |
|---|---|
| Which zones are still safe? | `NOT ST_DWithin(zone, flood, 120)` |
| Which villages are stranded? | `ST_Intersects(village, flood)` (spatial intersection) |
| Nearest safe zone per stranded village | `LEFT JOIN LATERAL (... ORDER BY pad <-> village LIMIT 1)` (**KNN** nearest-facility search with the GIST index) |
| Reachable? | distance <= 1300 m |
| Helicopter ETA | distance from Kamuzu International Airport to the landing spot / 180 km/h |
| Which roads are cut? | `ST_Intersection(road, flood)` |

**LATERAL** lets the subquery run once *per stranded village*, using that village's location.
The **`<->`** operator makes PostgreSQL walk the spatial index from the nearest zone outwards,
so it never has to measure the distance to every zone.
