# Reproducing the terrain analysis by hand in QGIS

`scripts/process_terrain.py` does this automatically. These steps redo the same analysis by
hand in QGIS (3.28 or newer), so you can take screenshots for the slide and show each method
step by step in the presentation.

All inputs are in `data/processed/` after running `scripts/load_data.py` and `scripts/process_terrain.py`:

| File | What it is |
|---|---|
| `dem_32736.tif` | Copernicus 30 m elevation, UTM 36S (metres) |
| `landcover_32736.tif` | ESA WorldCover 2021, 10 m classes |
| `sentinel_shield.gpkg` | every vector layer (villages, rivers, buildings, power lines, flood levels, landing zones, ...) |
| `slope_32736.tif`, `open_ground_32736.tif`, `hand_32736.tif` | the script's own outputs, to compare with yours |

**First:** set the project CRS to **EPSG:32736** (Project > Properties > CRS), so that all distances are in metres.

---

## 1. Slope analysis
1. Load `dem_32736.tif`.
2. **Raster > Analysis > Slope**. Input: the DEM, Z factor 1 (the DEM is already in metres), output: `my_slope.tif`.
3. Style it: Singleband pseudocolor, 0 to 15 degrees, green-yellow-red.
4. Screenshot: most of the Mponela plateau is under 7 degrees, while the river banks are steeper.

## 2. Land-cover reclassification
1. Load `landcover_32736.tif`. Its values are WorldCover classes: 10 trees, 20 shrubland, 30 grassland,
   40 cropland, 50 built-up, 60 bare, 80 water, 90 wetland.
2. **Processing Toolbox > Reclassify by table**. Table:

   | Min | Max | Value |
   |---|---|---|
   | 29 | 30 | 1 |
   | 39 | 40 | 1 |
   | 59 | 60 | 1 |

   Set "Range boundaries" to *min < value <= max*, and set "Use no data when no range matches" to **No**, with default value **0**.
   Output: `my_open_ground.tif` (1 = open ground, 0 = excluded).
3. Explain: helicopters can't land in trees or shrubs, and built-up, water and wetland are unsafe or soft.

## 3. Hazard buffers
1. Load the `buildings` and `power_lines` layers from `sentinel_shield.gpkg`.
2. **Vector > Geoprocessing > Buffer**: buildings 30 m, power lines 50 m, with "Dissolve result" ticked.
3. **Raster > Conversion > Rasterize**: burn value 1, 10 m resolution, extent = `landcover_32736.tif`,
   "Pre-initialize with" 0. Do this for both buffers: `my_bld.tif`, `my_pwr.tif`.

## 4. Multi-criteria suitability (Boolean overlay)
**Raster > Raster Calculator** (use the land-cover grid as the reference layer):
```
("my_slope@1" <= 7) * ("my_open_ground@1" = 1) * ("my_bld@1" = 0) * ("my_pwr@1" = 0)
```
Output `my_suitable.tif`, where 1 = every criterion is met. Compare it with the script's result.
QGIS resamples the 30 m slope onto the 10 m grid automatically. The script does the same with bilinear resampling.

## 5. From suitable cells to landing zones
1. **Raster > Conversion > Polygonize** `my_suitable.tif`, then keep only value = 1.
2. **Buffer** with distance **-25 m** and join style **Miter**. A polygon that still exists after this
   has room for a 50 x 50 m square, so you can delete the empty results.
3. (The script also cuts large areas into 250 m tiles and picks ~40 well-spread zones. Open the
   `candidate_lz` layer from the GeoPackage to see the final result.)

## 6. Flood model (HAND), optional
1. Load `hand_32736.tif`. Each cell holds its height in metres above the nearest river or stream cell.
2. Style it with singleband pseudocolor, 0 to 6 m, blues. Cells with HAND <= 3 are where a
   3 m flood would reach, if they connect to the river.
3. Compare with the `flood_extent` layer (filter: `"level_m" = 3`).

## 7. Nearest landing zone (check one by hand)
**Vector > Analysis Tools > Distance to nearest hub (line to hub)**: source = villages, destination =
`candidate_lz_pads`. This gives the same lines the app draws, before the flood filter is applied.

## Layout for the slide
Use the hillshade (`data/processed/hillshade.png`, or **Raster > Analysis > Hillshade** on the DEM),
with the flood at 4 m, safe zones in green, and villages coloured by status.
Add a scale bar and north arrow (Project > New Print Layout).
