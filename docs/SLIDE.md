# Slide content (one slide)

## SENTINEL SHIELD
### Finding safe helicopter landing zones as flood water rises: Kasangadzi River, Mponela, Dowa

**Problem**
- January 2024: flash floods on the Kasangadzi River cut off villages around Mponela.
- Helicopters need landing zones that are **flat, open, dry and clear of obstacles**, and these disappear as the water rises.
- Commanders need to know **who is stranded, where to land, and who can't be reached**.

**Spatial methods**
| Step | Method | Tool |
|---|---|---|
| Terrain | Slope from Copernicus 30 m DEM (<= 7°) | Python / QGIS |
| Land cover | Reclassify ESA WorldCover: grass, crop, bare = open | Python / QGIS |
| Obstacles | Buffers: buildings 30 m, power lines 50 m | Python / QGIS |
| Suitability | Boolean multi-criteria overlay, then the 50 x 50 m test (-25 m buffer) | Python / QGIS |
| Flood | HAND (height above nearest drainage), 0 to 6 m, river-connected | Python |
| Live safety | Zone **not within 120 m** of flood (`ST_DWithin`) | PostGIS |
| Stranded | Village footprint **intersects** flood (`ST_Intersects`) | PostGIS |
| Rescue | **Nearest facility (KNN)**: `LATERAL` + `<->` + GIST index; reach <= 1.3 km; ETA at 180 km/h from KIA | PostGIS |

**Architecture:** offline raster pre-processing, then PostGIS (EPSG:32736, GIST indexes), FastAPI, then React + Leaflet.
Each slider level is answered live in **about 50 ms**. Works fully **offline**.

**Data:** Copernicus GLO-30 · ESA WorldCover 2021 · OpenStreetMap · Microsoft Building Footprints · geoBoundaries

*Visual suggestion: a screenshot of the app at 4 m (flood, green H's, red/amber villages, rescue lines) on the right half.*
