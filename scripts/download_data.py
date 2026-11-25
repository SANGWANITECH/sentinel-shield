"""Download only the data the study area needs into data/raw.

    python scripts/download_data.py            # skip files that already exist
    python scripts/download_data.py --force    # re-download everything

Sources
- Dowa boundary: geoBoundaries MWI ADM2 (simplified file), filtered to Dowa.
- OSM layers: Overpass API, one small query per layer clipped to the study bbox.
  If every Overpass mirror fails, falls back to the OSM API /map bbox download.
- DEM: Copernicus GLO-30 COG on AWS, windowed read (only the bbox is fetched).
- Land cover: ESA WorldCover 10 m 2021 COG on AWS, windowed read.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import geopandas as gpd
import rasterio
from rasterio.merge import merge
from shapely.geometry import LineString, Point, Polygon, box, shape

from common import RAW, WGS84, bbox, load_config

CFG = load_config()
SRC = CFG["sources"]
W, S, E, N = bbox(CFG)

# Layer -> (Overpass filter, output modifier). "skel" drops tags we don't need (smaller download).
OSM_LAYERS = {
    "places":      ('node["place"~"^(town|village|hamlet|isolated_dwelling)$"]', "out geom qt;"),
    "roads":       ('way["highway"]', "out geom qt;"),
    "rivers":      ('way["waterway"~"^(river|stream|canal|drain|ditch)$"]', "out geom qt;"),
    "buildings":   ('way["building"]', "out skel geom qt;"),
    "power_lines": ('way["power"~"^(line|minor_line)$"]', "out geom qt;"),
    # Named features used to NAME settlements derived from building clusters (OSM has few
    # village nodes here). Schools/churches in Malawi usually carry the village name.
    "named_features": ('(nwr["place"]["name"];way["landuse"="residential"]["name"];'
                       'nwr["amenity"~"^(school|place_of_worship|clinic|hospital|marketplace)$"]["name"];)',
                       "out center qt;"),
}
KEEP_TAGS = {"places": ["name", "place", "population"], "roads": ["name", "highway"],
             "rivers": ["name", "waterway"], "buildings": [], "power_lines": ["voltage"],
             "named_features": ["name", "place", "amenity", "landuse"]}


def log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# HTTP via curl: on this connection Python's TLS handshakes often stall, curl's don't.
# ---------------------------------------------------------------------------
CURL = shutil.which("curl") or "curl"
TMP = RAW / "_cache"          # partial / intermediate downloads (git-ignored with data/raw)
TMP.mkdir(parents=True, exist_ok=True)


class HttpError(Exception):
    def __init__(self, code: int, msg: str = ""):
        super().__init__(f"HTTP {code} {msg}")
        self.code = code


def curl(url: str, out: Path, data_file: Path | None = None, params: dict | None = None,
         max_time: int = 900, resume: bool = False, retries: int = 1) -> Path:
    """Fetch url into out. data_file -> POST form field data=@file (Overpass).
    resume=True continues a partial file after a dropped connection (GET only)."""
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    cmd = [CURL, "-sS", "-L", "--connect-timeout", "60", "--max-time", str(max_time),
           "--speed-limit", "200", "--speed-time", "120", "-A", SRC["user_agent"],
           "-o", str(out), "-w", "%{http_code}"]
    if data_file:
        cmd += ["--data-urlencode", f"data@{data_file}"]
    if resume:
        cmd += ["-C", "-"]
    err = None
    for attempt in range(retries):
        p = subprocess.run(cmd + [url], capture_output=True, text=True)
        code = int(p.stdout.strip() or 0)
        if p.returncode == 0 and code in (200, 206):
            return out
        if resume and code == 416:          # already complete
            return out
        err = HttpError(code, f"curl exit {p.returncode} {p.stderr.strip()[:120]}")
        if code in (400, 404):
            break
        if attempt + 1 < retries:
            have = out.stat().st_size / 1e6 if out.exists() else 0
            log(f"    retry {attempt + 1}/{retries - 1} ({have:.1f} MB so far): {err}")
            time.sleep(10)
    raise err


# ---------------------------------------------------------------------------
# District boundary
# ---------------------------------------------------------------------------
def download_boundary(force: bool) -> None:
    out = RAW / "dowa_boundary.geojson"
    if out.exists() and not force:
        return log(f"[skip] {out.name}")
    d = CFG["district"]
    meta_file = curl(SRC["geoboundaries_api"].format(iso=d["country_iso"], level=d["adm_level"]),
                     TMP / "gb_meta.json")
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    # The simplified file is the smallest geoBoundaries offers (no clipped download exists).
    url = meta.get("simplifiedGeometryGeoJSON") or meta["gjDownloadURL"]
    log(f"[get ] geoBoundaries {d['adm_level']} (simplified) ...")
    gdf = gpd.read_file(curl(url, TMP / "gb_adm2.geojson"))
    dowa = gdf[gdf["shapeName"].str.lower() == d["name"].lower()]
    if dowa.empty:
        raise SystemExit(f"{d['name']} not found in geoBoundaries; names: {sorted(gdf.shapeName)}")
    dowa[["shapeName", "geometry"]].rename(columns={"shapeName": "name"}).to_file(out, driver="GeoJSON")
    log(f"[ok  ] {out.name}")


# ---------------------------------------------------------------------------
# OSM: Overpass first, OSM API fallback
# ---------------------------------------------------------------------------
def overpass(query: str) -> list[dict] | None:
    """Try each mirror with exponential backoff. Returns elements or None."""
    wait = 10
    qf = TMP / "overpass_query.txt"
    qf.write_text(query, encoding="utf-8")
    for attempt in range(3):
        for url in SRC["overpass_mirrors"]:
            try:
                out = curl(url, TMP / "overpass_result.json", data_file=qf, max_time=400)
                js = json.loads(out.read_text(encoding="utf-8"))
                if "error" in js.get("remark", "").lower():
                    raise RuntimeError(js["remark"])
                return js["elements"]
            except Exception as ex:  # network errors, timeouts, bad JSON
                log(f"    {url} -> {type(ex).__name__}: {str(ex)[:80]}")
        log(f"    all mirrors failed, retrying in {wait}s ...")
        time.sleep(wait)
        wait *= 2
    return None


def osm_api_elements() -> list[dict]:
    """Fallback: download the bbox from the main OSM API (XML), splitting into tiles if
    the server says there are too many nodes. Returns Overpass-style elements with geometry."""
    nodes, ways = {}, []

    def fetch(w, s, e, n, depth=0):
        log(f"    OSM API bbox {w:.4f},{s:.4f},{e:.4f},{n:.4f}")
        out = TMP / f"osm_api_{w:.4f}_{s:.4f}_{e:.4f}_{n:.4f}.xml"
        if not out.exists():
            try:
                curl(SRC["osm_api_map"], out.with_suffix(".part"), params={"bbox": f"{w},{s},{e},{n}"},
                     max_time=7200)
                out.with_suffix(".part").rename(out)
            except HttpError as ex:
                if ex.code == 400 and depth < 4:      # too many nodes -> split in 4
                    mx, my = (w + e) / 2, (s + n) / 2
                    for b in [(w, s, mx, my), (mx, s, e, my), (w, my, mx, n), (mx, my, e, n)]:
                        fetch(*b, depth + 1)
                    return
                raise
        for _, el in ET.iterparse(out, events=("end",)):
            if el.tag == "node":
                nodes[el.get("id")] = {"type": "node", "lat": float(el.get("lat")),
                                       "lon": float(el.get("lon")),
                                       "tags": {t.get("k"): t.get("v") for t in el.iter("tag")}}
                el.clear()
            elif el.tag == "way":
                ways.append({"id": el.get("id"), "refs": [nd.get("ref") for nd in el.iter("nd")],
                             "tags": {t.get("k"): t.get("v") for t in el.iter("tag")}})
                el.clear()

    fetch(W, S, E, N)
    out = [n for n in nodes.values() if n["tags"]]
    seen = set()
    for w in ways:
        if w["id"] in seen:
            continue
        seen.add(w["id"])
        geom = [{"lat": nodes[r]["lat"], "lon": nodes[r]["lon"]} for r in w["refs"] if r in nodes]
        out.append({"type": "way", "tags": w["tags"], "geometry": geom})
    return out


def matches(layer: str, tags: dict, etype: str) -> bool:
    """Same filters as OSM_LAYERS, applied in Python to fallback data."""
    if layer == "places":
        return etype == "node" and tags.get("place") in {"town", "village", "hamlet", "isolated_dwelling"}
    if layer == "named_features":
        return "name" in tags and ("place" in tags or tags.get("landuse") == "residential" or tags.get(
            "amenity") in {"school", "place_of_worship", "clinic", "hospital", "marketplace"})
    if etype != "way":
        return False
    return {"roads": "highway" in tags,
            "rivers": tags.get("waterway") in {"river", "stream", "canal", "drain", "ditch"},
            "buildings": "building" in tags,
            "power_lines": tags.get("power") in {"line", "minor_line"}}[layer]


def to_gdf(layer: str, elements: list[dict]) -> gpd.GeoDataFrame:
    rows = []
    for el in elements:
        tags = el.get("tags", {})
        if el["type"] == "node":
            geom = Point(el["lon"], el["lat"])
        elif layer == "named_features":
            if "center" in el:
                geom = Point(el["center"]["lon"], el["center"]["lat"])
            else:  # fallback data: use the first vertex
                pts = el.get("geometry") or []
                if not pts:
                    continue
                geom = Point(pts[0]["lon"], pts[0]["lat"])
        else:
            pts = [(p["lon"], p["lat"]) for p in el.get("geometry", []) if p]
            if len(pts) < 2:
                continue
            if layer == "buildings":
                if len(pts) < 4 or pts[0] != pts[-1]:
                    continue
                geom = Polygon(pts)
            else:
                geom = LineString(pts)
        rows.append({**{k: tags.get(k) for k in KEEP_TAGS[layer]}, "geometry": geom})
    cols = KEEP_TAGS[layer] + ["geometry"]
    return gpd.GeoDataFrame(rows, columns=cols, geometry="geometry", crs=WGS84)


def download_osm(force: bool) -> None:
    todo = [l for l in OSM_LAYERS if force or not (RAW / f"osm_{l}.geojson").exists()]
    for l in OSM_LAYERS:
        if l not in todo:
            log(f"[skip] osm_{l}.geojson")
    fallback = None
    for layer in todo:
        filt, out_stmt = OSM_LAYERS[layer]
        log(f"[get ] OSM {layer} via Overpass ...")
        q = f"[out:json][timeout:180][bbox:{S},{W},{N},{E}];{filt};{out_stmt}"
        els = overpass(q)
        if els is None:
            if fallback is None:
                log("[warn] Overpass unavailable - falling back to OSM API bbox download (slow)")
                fallback = osm_api_elements()
            els = [e for e in fallback if matches(layer, e.get("tags", {}), e["type"])]
        gdf = to_gdf(layer, els)
        gdf.to_file(RAW / f"osm_{layer}.geojson", driver="GeoJSON")
        log(f"[ok  ] osm_{layer}.geojson  ({len(gdf)} features)")


# ---------------------------------------------------------------------------
# Microsoft Global ML Building Footprints (OSM buildings are sparse here)
# ---------------------------------------------------------------------------
def quadkey(lat: float, lon: float, z: int) -> str:
    """Bing-maps quadkey of the tile containing lat/lon at zoom z."""
    s = math.sin(math.radians(lat))
    x = int((lon + 180) / 360 * 2 ** z)
    y = int((0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * 2 ** z)
    key = ""
    for i in range(z, 0, -1):
        mask = 1 << (i - 1)
        key += str((1 if x & mask else 0) + (2 if y & mask else 0))
    return key


def download_ms_buildings(force: bool) -> None:
    out = RAW / "ms_buildings.geojson"
    if out.exists() and not force:
        return log(f"[skip] {out.name}")
    import csv
    import gzip

    idx = TMP / "ms_dataset_links.csv"
    if not idx.exists():
        log("[get ] Microsoft buildings index (7 MB) ...")
        curl(SRC["ms_buildings_index"], idx.with_suffix(".part"), max_time=3600, resume=True, retries=30)
        idx.with_suffix(".part").rename(idx)
    keys = {quadkey(lat, lon, 9) for lat in (S, N) for lon in (W, E)}
    with open(idx, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["QuadKey"] in keys]
    if not rows:
        raise RuntimeError(f"no Microsoft building tiles for quadkeys {keys}")
    area = box(W, S, E, N)
    feats = []
    for r in rows:
        gz = TMP / f"ms_{r['Location']}_{r['QuadKey']}.csv.gz"
        if not gz.exists():
            log(f"[get ] MS buildings {r['Location']} tile {r['QuadKey']} ({r.get('Size', '?')})")
            curl(r["Url"], gz.with_suffix(".part"), max_time=7200, resume=True, retries=30)
            gz.with_suffix(".part").rename(gz)
        with gzip.open(gz, "rt", encoding="utf-8") as f:
            for line in f:
                g = shape(json.loads(line)["geometry"])
                if g.intersects(area):
                    feats.append(g)
    gdf = gpd.GeoDataFrame(geometry=feats, crs=WGS84)
    gdf.to_file(out, driver="GeoJSON")
    log(f"[ok  ] {out.name}  ({len(gdf)} buildings)")


# ---------------------------------------------------------------------------
# Rasters: windowed reads of cloud-optimised GeoTIFFs
# ---------------------------------------------------------------------------
GDAL_ENV = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",   # don't list the bucket
                CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
                GDAL_HTTP_MAX_RETRY="5", GDAL_HTTP_RETRY_DELAY="5",
                GDAL_HTTP_TIMEOUT="300", VSI_CACHE="TRUE")


def padded_bbox():
    m = CFG["study_area"]["raster_margin_m"]
    dlat = m / 111_320
    dlon = m / (111_320 * math.cos(math.radians((S + N) / 2)))
    return W - dlon, S - dlat, E + dlon, N + dlat


def tile_urls(template: str, step: int) -> list[str]:
    """URLs of every tile (step x step degrees, named by lower-left corner) touching the bbox."""
    w, s, e, n = padded_bbox()
    urls = []
    for lat in range(math.floor(s / step) * step, math.ceil(n / step) * step, step):
        for lon in range(math.floor(w / step) * step, math.ceil(e / step) * step, step):
            urls.append(template.format(ns="S" if lat < 0 else "N", lat=abs(lat),
                                        ew="W" if lon < 0 else "E", lon=abs(lon)))
    return urls


def download_raster(name: str, template: str, step: int, force: bool) -> None:
    out = RAW / f"{name}.tif"
    if out.exists() and not force:
        return log(f"[skip] {out.name}")
    urls = tile_urls(template, step)
    log(f"[get ] {name}: windowed read from {len(urls)} tile(s)")
    for u in urls:
        log(f"    {u}")
    with rasterio.Env(**GDAL_ENV):
        srcs = [rasterio.open(u) for u in urls]
        # merge(bounds=...) only requests the internal COG blocks that overlap the window.
        data, transform = merge(srcs, bounds=padded_bbox())
        profile = srcs[0].profile.copy()
        for s_ in srcs:
            s_.close()
    profile.update(driver="GTiff", height=data.shape[1], width=data.shape[2], transform=transform,
                   compress="deflate", tiled=True, blockxsize=256, blockysize=256)
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(data)
    log(f"[ok  ] {out.name}  {data.shape[2]}x{data.shape[1]} px, {out.stat().st_size/1e6:.2f} MB")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="re-download existing files")
    ap.add_argument("--only", choices=["boundary", "osm", "dem", "landcover", "msbuildings"], nargs="*")
    a = ap.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    log(f"Study area bbox (W,S,E,N): {W}, {S}, {E}, {N}")
    steps = {
        "boundary": lambda: download_boundary(a.force),
        "dem": lambda: download_raster("dem_cop30", SRC["copernicus_dem"], 1, a.force),
        "landcover": lambda: download_raster("worldcover_2021", SRC["worldcover"], 3, a.force),
        "osm": lambda: download_osm(a.force),
        "msbuildings": lambda: download_ms_buildings(a.force),
    }
    failed = []
    for name, fn in steps.items():
        if a.only and name not in a.only:
            continue
        try:
            fn()
        except Exception as ex:
            log(f"[FAIL] {name}: {type(ex).__name__}: {ex}")
            failed.append(name)
    if failed:
        sys.exit(f"Failed steps: {failed} - rerun to retry (finished files are skipped).")


if __name__ == "__main__":
    main()
