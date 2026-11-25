# Live demo script (3 minutes)

**Before the session (5 min early)**
1. Start PostgreSQL (it normally starts with Windows).
2. `.\scripts\start.ps1 -Preview` opens the app at http://localhost:4173. The terminal should say "Backend OK".
3. Press **Reset** so the slider is at 0 m. Make the browser full screen (F11). Close other tabs.
4. Backup plan: if the laptop has no internet, nothing changes, because the app is fully offline. If the database is missing,
   run `.\scripts\restore_db.ps1` (takes seconds).

---

### 0:00 to 0:30: The problem
> "In January 2024 flash floods on the Kasangadzi River cut off villages around Mponela in Dowa.
> A rescue helicopter needs somewhere safe to land: flat, open, dry, away from buildings and power lines.
> As the water rises, those places disappear. Sentinel Shield finds them in real time."

Point at the map: the hillshade terrain, the river, Mponela town in the middle, and the green **H** markers (40 candidate landing zones).

### 0:30 to 1:00: What is precomputed
> "Before the demo we did the raster analysis offline: slope from the Copernicus 30 m DEM,
> land-cover reclassification from ESA WorldCover, buffers around buildings, and a multi-criteria
> overlay. That gave these 40 zones, each with room for a 50 by 50 metre pad. We also modelled
> flood extents from 0 to 6 metres using HAND, the height above the nearest river."

Point at the **criteria chips** at the bottom of the panel.

### 1:00 to 2:00: Rising water (the live part)
Click **Simulate rising water**. While it rises:
> "Every half metre, the browser asks PostGIS for that water level. The database checks which zones
> are still more than 120 metres from the flood, which villages the flood touches, and finds
> each stranded village's nearest safe zone with a K-nearest-neighbour search. Each query takes
> about 50 milliseconds." (Point at "PostGIS xx ms" at the bottom of the panel.)

Point out as it happens:
- The **blue flood** spreading along the valley, and **ROAD CUT** labels appearing.
- Zones near the river turning into **dashed grey** H's (lost).
- Villages turning **amber** with a **dashed green line** to their landing zone.
- **Red pulsing** villages: no safe zone within 1.3 km.

### 2:00 to 2:40: Read the result
Let it stop at 6 m (or drag the slider to 4 m). Read the counters:
> "At 6 metres about 16,000 people are exposed and 11 settlements are stranded. 7 can be reached by
> helicopter. The list is sorted by population, so the commander starts with the biggest. For example,
> Settlement 25 goes to LZ-29, 0.7 km away, about 10 minutes' flight from Kamuzu International Airport.
> These 4 red ones have no safe landing zone within 1.3 km, so they need a boat team instead."

Hover a red village and a lost zone to show the tooltips.

### 2:40 to 3:00: Close
> "Heavy raster work is done offline, light vector queries run live in PostGIS, so the decision
> support updates instantly. The whole system runs offline on a laptop, which matters in a disaster
> when networks are down. Questions?"

---

**If something goes wrong**
| Symptom | Fix |
|---|---|
| Red banner "Cannot reach the API" | The backend window closed. Run `.\scripts\start.ps1` again. |
| Badge "PostGIS live" is red | PostgreSQL isn't running. Start the `postgresql-x64-17` service. |
| Map is blank | Hard-refresh with Ctrl+F5. |
| Need a specific scene fast | Open `http://localhost:4173/?level=4` |
