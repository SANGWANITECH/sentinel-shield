# Q&A preparation: 25 likely questions

## A. Problem and scope
**1. Why this area?**
The Kasangadzi River around Mponela, Dowa, had real flash floods in January 2024 that cut off villages.
It's small enough (10 x 8 km) to process quickly and to demo live, and it's a real place with real data.

**2. Who would use this system?**
A disaster coordination cell (e.g. DoDMA, the Malawi Defence Force air wing, or NGOs) planning helicopter
rescues. It answers three questions: who is stranded, where can we land, and who needs another method (boats).

**3. Why "cyclone rescue" if you model a river flood?**
Cyclones (e.g. Freddy in 2023) cause flooding through extreme rainfall. The system models the flood
hazard that a cyclone or heavy storm creates, so the decision problem is the same.

## B. Data
**4. Where does the data come from?**
Copernicus GLO-30 DEM and ESA WorldCover 2021, both read from AWS open data. Villages, roads, rivers and
buildings come from OpenStreetMap, the district boundary from geoBoundaries, and extra buildings from
Microsoft Global ML Building Footprints.

**5. How did you handle the slow internet?**
*Windowed reads*: the DEM and land cover are Cloud-Optimised GeoTIFFs, so we downloaded only the blocks
covering our box (about 400 KB in total instead of hundreds of MB). OSM came as small per-layer
Overpass queries. Every download resumes and is skipped if already present.

**6. OSM had almost no villages. How did you get 49?**
OSM had a single place node (Mponela). We clustered buildings: buildings within 100 m of each other form a
cluster, and clusters with at least 40 buildings count as settlements. Mponela town is split into 1 km sectors.
Names come from the nearest named OSM feature, otherwise "Settlement NN". All the thresholds are in config.toml.

**7. How did you estimate population?**
Voronoi polygons around the settlements. We count the buildings in each cell and multiply by 4.4 people per
building, the 2018 Census average household size, assuming about one household per building. It's an estimate:
non-residential buildings, multi-building compounds and unmapped buildings all introduce error.

**8. Why add Microsoft buildings?**
OSM had only 831 buildings in 80 km², so population and settlements would have been badly
underestimated. Microsoft's machine-learning footprints added 14,110 more, with overlaps removed.

## C. Methods
**9. Why 7 degrees maximum slope?**
Helicopters need near-level ground for skids or wheels. Limits depend on the aircraft (roughly 5 to 10
degrees). We chose a conservative 7 degrees, which can be changed in config.toml.

**10. Why exclude trees, shrubs, built-up, water and wetland?**
Trees and shrubs are rotor obstacles, built-up areas have buildings and wires, and water or wetland can't
carry the aircraft's weight. Grassland, cropland and bare ground are open and firm.

**11. How do you guarantee a 50 x 50 m landing area?**
We shrink each suitable polygon inwards by 25 m (a negative buffer with square corners). If anything
survives, a 50 x 50 m square fits inside. Because the polygons come from raster cells, their edges run
north-south and east-west, so this test is exact.

**12. Why only 40 landing zones?**
The flat cropland plateau is about 70% suitable, which is far too much to show. We cut it into 250 m tiles,
scored each tile (60% flatness, 40% size) and picked the best 40 with at least 1.2 km between them, so they
cover the whole area instead of clustering.

**13. Explain HAND.**
Height Above Nearest Drainage: for every DEM cell we find the nearest river cell and subtract that river
cell's elevation. A cell with HAND = 2 m sits 2 m above its nearest river, so it floods when the water
rises 2 m. We also require flooded cells to connect to the river.

**14. Is that a real flood model?**
No. It is a simplified terrain-based scenario model, not a hydraulic model. It ignores rainfall, discharge,
flow speed, timing and structures like bridges. A hydraulic model (HEC-RAS, LISFLOOD) needs river
cross-sections and gauge data we don't have. HAND is a recognised quick method for flood-exposure screening.

**15. Why 120 m from water?**
A safety margin for rising water, soft saturated banks and rotor downwash spray. It's set in config.toml.
We apply it conservatively: if any part of a zone is within 120 m, the whole zone is "lost".

**16. How is ETA calculated?**
Straight-line distance from Kamuzu International Airport (staging base) to the landing spot, divided by an
assumed cruise speed of 180 km/h. The landing zones are 25 to 34 km from KIA, so ETAs are about 8 to 11 minutes. It ignores
start-up time, weather and routing.

## D. Database design
**17. Why EPSG:32736 and not 4326?**
32736 (UTM 36S) uses metres, so distances and areas are directly correct. In 4326 the units are degrees, and a
degree of longitude isn't a fixed distance. We convert to 4326 only when sending GeoJSON to the browser.

**18. What are GIST indexes and why do you need them?**
A GIST spatial index is an R-tree of bounding boxes. Spatial filters such as ST_Intersects, ST_DWithin and
`<->` first discard everything whose box can't match, so the database only does exact geometry tests on a
few candidates. Every geometry column has one.

**19. Explain LATERAL and `<->`.**
For each stranded village, `LEFT JOIN LATERAL (SELECT ... FROM candidate_lz WHERE safe ORDER BY pad <-> village LIMIT 1)`
finds its nearest safe landing zone. LATERAL lets the subquery use the current village's location.
`<->` is the distance operator the GIST index supports, so PostgreSQL walks the index from the nearest
zone outwards. EXPLAIN shows "Index Scan using candidate_lz_pad_gix, Order By: (pad <-> geom)".

**20. Why do you precompute floods but compute safety live?**
Raster analysis (slope, HAND) takes seconds to minutes and doesn't change between slider moves.
Which zones are safe and who is stranded at a given level is a fast vector query (40 to 84 ms here), and
computing it live means changing an assumption, such as clearance or reach, needs no re-processing.

**21. What is the materialized view for?**
`flood_extent_parts` cuts each flood polygon into pieces of at most 255 vertices (ST_Subdivide). A single big
polygon has one huge bounding box, so the index can't filter anything. Small pieces have tight boxes, so the
index skips pieces that are far away.

## E. Code
**22. Walk us through what happens when I move the slider.**
React updates the level, then calls `GET /api/scenario/3.5`. FastAPI runs `SELECT sentinel_scenario(3.5)`,
PostGIS builds a GeoJSON document (flood, safe and lost zones, stranded villages with their nearest zone,
links, road cuts, totals), FastAPI returns it unchanged, and Leaflet restyles the layers with CSS transitions.

**23. How do you know the results are correct?**
pytest tests check that totals add up (stranded = reachable + no safe zone), that the flood grows with level,
that coordinates are valid lon/lat, and that unknown levels return 404. We checked that all landing spots are
at least 90 m from buildings, have slope <= 3.4 degrees and are valid geometries. The same analysis can be
reproduced by hand in QGIS (qgis/README.md).

## F. Limitations and improvements
**24. What are the main limitations?**
- HAND is not hydraulic. The DEM is a 30 m *surface* model (trees and roofs raise it).
- Only OSM rivers are drainage, with no small streams mapped, so the flood only follows the main river.
- Population is estimated from building counts.
- There are no OSM power lines in the area, so that criterion excludes nothing here.
- ETA is straight-line flight with a constant speed.
- "Stranded" means the flood touches the village's built-up area. We don't model whether roads out are still open.

**25. What would you improve with more time?**
A real hydraulic model with gauge data, drainage derived from the DEM (flow accumulation), field-verified
landing zones and village names, live rainfall and river-gauge feeds, multiple staging bases and helicopter
capacity (how many trips), and road-network accessibility for boats and vehicles.
