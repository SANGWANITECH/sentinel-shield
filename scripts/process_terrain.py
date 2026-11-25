"""Offline raster analysis: slope, land-cover reclass, landing-zone suitability, HAND flood levels.

    python scripts/process_terrain.py

Reads the DEM / land cover made by load_data.py (data/processed/*_32736.tif) and the vector
layers from PostGIS. Writes candidate_lz and flood_extent (cleared and reloaded), plus:
  data/processed/slope_32736.tif, suitability_32736.tif, hand_32736.tif
  data/processed/sentinel_shield.gpkg        (all layers, for QGIS)
  frontend/public/basemap/hillshade.png/.json (offline basemap + its lat/lon bounds)
"""
from __future__ import annotations

import json
import math
import shutil

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import shapely
from rasterio import features
from rasterio.warp import Resampling, reproject
from shapely.geometry import MultiPolygon, box, shape
from shapely.ops import polylabel
from sqlalchemy import create_engine, text

from common import PROCESSED, RAW, ROOT, UTM36S, db_url, load_config

CFG = load_config()
T, F = CFG["terrain"], CFG["flood"]
ENGINE = create_engine(db_url())


def log(msg: str) -> None:
    print(msg, flush=True)


def read_table(table: str, where: str = "") -> gpd.GeoDataFrame:
    return gpd.read_postgis(f"SELECT * FROM {table} {where}", ENGINE, geom_col="geom")


def save_tif(path, arr, ref_profile, transform, dtype, nodata):
    prof = ref_profile.copy()
    prof.update(driver="GTiff", dtype=dtype, count=1, nodata=nodata, transform=transform,
                height=arr.shape[0], width=arr.shape[1], compress="deflate", tiled=True,
                blockxsize=256, blockysize=256)
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(arr.astype(dtype), 1)


def rasterize(geoms, shape_, transform, all_touched=True) -> np.ndarray:
    geoms = [g for g in geoms if g is not None and not g.is_empty]
    if not geoms:
        return np.zeros(shape_, bool)
    return features.rasterize(((g, 1) for g in geoms), out_shape=shape_, transform=transform,
                              all_touched=all_touched, dtype="uint8").astype(bool)


def to_multi(g):
    if g is None or g.is_empty:
        return None
    if g.geom_type == "Polygon":
        return MultiPolygon([g])
    if g.geom_type == "MultiPolygon":
        return g
    polys = [p for p in shapely.get_parts(g) if p.geom_type == "Polygon"]
    return MultiPolygon(polys) if polys else None


# ---------------------------------------------------------------------------
# 1. Slope
# ---------------------------------------------------------------------------
def compute_slope(dem: np.ndarray, transform, nodata) -> np.ndarray:
    """Slope in degrees. The DEM is in UTM so cell size and elevation are both metres."""
    z = np.where(dem == nodata, np.nan, dem).astype("float64")
    dz_drow, dz_dcol = np.gradient(z, abs(transform.e), transform.a)
    return np.degrees(np.arctan(np.hypot(dz_dcol, dz_drow))).astype("float32")


# ---------------------------------------------------------------------------
# 3. Candidate landing zones
# ---------------------------------------------------------------------------
def candidate_landing_zones(slope_lc, lc, lc_tr, study_geom, buildings, power):
    shape_ = lc.shape
    open_ground = np.isin(lc, T["open_landcover"])
    flat = np.nan_to_num(slope_lc, nan=99) <= T["max_slope_deg"]
    near_bld = rasterize(buildings.geometry.buffer(T["building_clearance_m"]), shape_, lc_tr)
    near_pwr = rasterize(power.geometry.buffer(T["powerline_clearance_m"]), shape_, lc_tr)
    in_study = rasterize([study_geom], shape_, lc_tr, all_touched=False)
    suitable = open_ground & flat & ~near_bld & ~near_pwr & in_study
    stats = {"cells_open": int((open_ground & in_study).sum()), "cells_flat": int((flat & in_study).sum()),
             "cells_suitable": int(suitable.sum()), "cell_m": lc_tr.a}
    log(f"  suitable cells: {stats['cells_suitable']} of {int(in_study.sum())} in study area")

    polys = [shape(g) for g, v in features.shapes(suitable.astype("uint8"), mask=suitable,
                                                  transform=lc_tr, connectivity=4) if v == 1]
    gdf = gpd.GeoDataFrame(geometry=polys, crs=UTM36S)
    half = T.get("min_lz_side_m", CFG["rescue"]["min_lz_side_m"]) / 2

    def fits(geoms):
        # Mitre-joined negative buffer of axis-aligned raster edges = erosion by an axis-aligned
        # 50 x 50 m square: non-empty means a 50 x 50 m pad fits somewhere inside.
        return ~shapely.is_empty(shapely.buffer(np.asarray(geoms), -half, join_style="mitre"))

    gdf = gdf[gdf.area >= (2 * half) ** 2]
    gdf = gdf[fits(gdf.geometry.values)]
    # Tile big open areas so each zone is a compact site.
    t = T["lz_tile_m"]
    minx, miny, maxx, maxy = study_geom.bounds
    tiles = gpd.GeoDataFrame(geometry=[box(x, y, x + t, y + t)
                                       for x in np.arange(minx, maxx, t) for y in np.arange(miny, maxy, t)],
                             crs=UTM36S)
    pieces = gpd.overlay(gdf, tiles, how="intersection", keep_geom_type=True).explode(index_parts=False)
    pieces = pieces[pieces.area >= (2 * half) ** 2]
    pieces = pieces[fits(pieces.geometry.values)].reset_index(drop=True)
    stats["pieces_fit_50x50"] = len(pieces)
    log(f"  tile pieces where a 50x50 m pad fits: {len(pieces)}")
    if pieces.empty:
        return pieces, stats

    # Zonal statistics: mean slope and majority land cover per piece.
    ids = features.rasterize(((g, i + 1) for i, g in enumerate(pieces.geometry)), out_shape=shape_,
                             transform=lc_tr, all_touched=False, dtype="int32")
    n = len(pieces) + 1
    m = ids > 0
    cnt = np.bincount(ids[m], minlength=n)
    ssum = np.bincount(ids[m], weights=np.nan_to_num(slope_lc[m]), minlength=n)
    pieces["mean_slope"] = (ssum / np.maximum(cnt, 1))[1:]
    classes = T["open_landcover"]
    votes = np.stack([np.bincount(ids[m], weights=(lc[m] == c), minlength=n)[1:] for c in classes])
    names = {30: "grassland", 40: "cropland", 60: "bare / sparse vegetation"}
    pieces["landcover"] = [names.get(classes[k], str(classes[k])) for k in votes.argmax(axis=0)]
    pieces["area_m2"] = pieces.area
    # Landing spot = pole of inaccessibility of the eroded shape (most clearance on all sides).
    pads = []
    for g in pieces.geometry:
        core = g.buffer(-half, join_style="mitre")
        core = max(shapely.get_parts(core), key=lambda p: p.area) if core.geom_type != "Polygon" else core
        pads.append(polylabel(core, tolerance=1.0))
    pieces["pad"] = gpd.GeoSeries(pads, crs=UTM36S)
    # Score: flatter and larger is better.
    pieces["score"] = (0.6 * (1 - pieces["mean_slope"] / T["max_slope_deg"]).clip(0, 1)
                       + 0.4 * (pieces["area_m2"] / t ** 2).clip(0, 1))

    # Spread selection: greedy by score with a minimum spacing between landing spots.
    order = pieces.sort_values("score", ascending=False).index.to_list()
    xy = np.array([(p.x, p.y) for p in pieces["pad"]])
    chosen, used_spacing = [], None
    for sp in T["lz_spacing_m"]:
        sel = []
        for i in order:
            if all(np.hypot(*(xy[i] - xy[j])) >= sp for j in sel):
                sel.append(i)
                if len(sel) >= T["max_lz"]:
                    break
        chosen, used_spacing = sel, sp
        if len(sel) >= T["max_lz"]:
            break
    stats.update(lz_selected=len(chosen), lz_spacing_used_m=used_spacing)
    log(f"  selected {len(chosen)} zones with >= {used_spacing} m spacing")
    out = pieces.loc[chosen].reset_index(drop=True)
    out["code"] = [f"LZ-{i + 1:02d}" for i in range(len(out))]
    out["geometry"] = [to_multi(g) for g in out.geometry]
    return out, stats


# ---------------------------------------------------------------------------
# 4. HAND flood model
# ---------------------------------------------------------------------------
def flood_levels(dem, dem_tr, nodata, rivers, study_geom):
    valid = dem != nodata
    river_lines = rivers[rivers["waterway"].isin(F["drainage_waterways"])].geometry
    river = rasterize(river_lines, dem.shape, dem_tr) & valid
    log(f"  drainage cells: {int(river.sum())} (from {len(river_lines)} river/stream lines)")
    rows, cols = np.nonzero(valid)
    xs, ys = rasterio.transform.xy(dem_tr, rows, cols)
    pts = shapely.points(np.asarray(xs), np.asarray(ys))
    rr, rc = np.nonzero(river)
    tree = shapely.STRtree(pts[river[rows, cols]])
    # Nearest drainage cell for every cell (same result as distance_transform_edt indices).
    q_idx, t_idx = tree.query_nearest(pts, all_matches=False)
    river_z = dem[rr, rc].astype("float64")
    # pts[river[rows, cols]] is ordered like np.nonzero(river) (both row-major), so t_idx maps to rr/rc.
    hand = np.full(dem.shape, np.nan, dtype="float32")
    hand[rows[q_idx], cols[q_idx]] = dem[rows[q_idx], cols[q_idx]] - river_z[t_idx]

    river_union = shapely.union_all(np.asarray(river_lines.values))
    out = []
    for lvl in F["levels_m"]:
        mask = valid & ((np.nan_to_num(hand, nan=1e9) <= lvl) | river)
        polys = [shape(g) for g, v in features.shapes(mask.astype("uint8"), mask=mask,
                                                      transform=dem_tr, connectivity=8) if v == 1]
        # Keep only water connected to the river network (no isolated low spots).
        polys = [p for p in polys if p.intersects(river_union)]
        g = shapely.union_all(polys) if polys else None
        if g is None or g.is_empty:
            continue
        s = F["smooth_m"]
        g = g.buffer(s).buffer(-s).simplify(F["simplify_m"]).intersection(study_geom)
        g = to_multi(shapely.make_valid(g))
        if g is None:
            continue
        out.append({"level_m": lvl, "area_m2": g.area, "geometry": g})
        log(f"  level {lvl:>3} m: {g.area / 1e6:6.2f} km2")
    return gpd.GeoDataFrame(out, geometry="geometry", crs=UTM36S), hand


# ---------------------------------------------------------------------------
# 5. Hillshade basemap (PNG in EPSG:4326 so Leaflet can stretch it over lat/lon bounds)
# ---------------------------------------------------------------------------
def hillshade_png():
    up = 3   # read the DEM at 3x resolution (bilinear) so the basemap stays smooth when zoomed in
    with rasterio.open(RAW / "dem_cop30.tif") as src:
        z = src.read(1, out_shape=(src.height * up, src.width * up),
                     resampling=Resampling.bilinear).astype("float64")
        b = src.bounds
        lat = math.radians((b.top + b.bottom) / 2)
        dx = src.res[0] / up * 111_320 * math.cos(lat)
        dy = src.res[1] / up * 110_574
        if src.nodata is not None:
            z[z == src.nodata] = np.nan
    zf = 3.0  # vertical exaggeration: the Mponela plateau is gentle
    dz_drow, dz_dcol = np.gradient(z * zf, dy, dx)
    nx, ny = -dz_dcol, dz_drow             # rows run north->south, so d/dnorth = -d/drow
    norm = np.sqrt(nx ** 2 + ny ** 2 + 1)
    az, alt = math.radians(315), math.radians(45)
    lx, ly, lz = math.sin(az) * math.cos(alt), math.cos(az) * math.cos(alt), math.sin(alt)
    hs = np.clip((nx * lx + ny * ly + lz) / norm, 0, 1)
    hs = np.nan_to_num(hs, nan=0.0)
    hs = hs ** 1.4                         # a little more contrast for the dark theme
    dark, light = np.array([7, 11, 16]), np.array([70, 88, 102])
    rgb = (dark[:, None, None] + (light - dark)[:, None, None] * hs[None]).astype("uint8")
    alpha = np.full(hs.shape, 255, "uint8")
    out_dir = ROOT / "frontend" / "public" / "basemap"
    out_dir.mkdir(parents=True, exist_ok=True)
    png = PROCESSED / "hillshade.png"
    with rasterio.open(png, "w", driver="PNG", width=hs.shape[1], height=hs.shape[0], count=4,
                       dtype="uint8") as dst:
        dst.write(np.concatenate([rgb, alpha[None]]))
    bounds = {"south": b.bottom, "west": b.left, "north": b.top, "east": b.right}
    (PROCESSED / "hillshade.json").write_text(json.dumps(bounds, indent=2))
    shutil.copy(png, out_dir / "hillshade.png")
    shutil.copy(PROCESSED / "hillshade.json", out_dir / "hillshade.json")
    for extra in png.parent.glob("hillshade.png.aux.xml"):
        extra.unlink()
    log(f"  hillshade.png {hs.shape[1]}x{hs.shape[0]} px -> frontend/public/basemap/")


# ---------------------------------------------------------------------------
def main() -> None:
    PROCESSED.mkdir(parents=True, exist_ok=True)
    log("Reading inputs ...")
    with rasterio.open(PROCESSED / "dem_32736.tif") as s:
        dem, dem_tr, dem_nd, dem_prof = s.read(1), s.transform, s.nodata, s.profile
    with rasterio.open(PROCESSED / "landcover_32736.tif") as s:
        lc, lc_tr, lc_prof = s.read(1), s.transform, s.profile
    study_geom = read_table("study_area").geometry.iloc[0]
    buildings, power, rivers = read_table("buildings"), read_table("power_lines"), read_table("rivers")

    log("1. Slope ...")
    slope = compute_slope(dem, dem_tr, dem_nd)
    save_tif(PROCESSED / "slope_32736.tif", np.nan_to_num(slope, nan=-9999), dem_prof, dem_tr, "float32", -9999)
    slope_lc = np.full(lc.shape, np.nan, "float32")
    reproject(slope, slope_lc, src_transform=dem_tr, src_crs=UTM36S, dst_transform=lc_tr, dst_crs=UTM36S,
              resampling=Resampling.bilinear, src_nodata=np.nan, dst_nodata=np.nan)

    log("2-3. Land-cover reclass + suitability -> candidate landing zones ...")
    lz, lz_stats = candidate_landing_zones(slope_lc, lc, lc_tr, study_geom, buildings, power)
    open_ground = np.isin(lc, T["open_landcover"]).astype("uint8")
    save_tif(PROCESSED / "open_ground_32736.tif", open_ground, lc_prof, lc_tr, "uint8", 255)

    log("4. HAND flood levels ...")
    floods, hand = flood_levels(dem, dem_tr, dem_nd, rivers, study_geom)
    save_tif(PROCESSED / "hand_32736.tif", np.nan_to_num(hand, nan=-9999), dem_prof, dem_tr, "float32", -9999)

    log("Loading candidate_lz and flood_extent ...")
    with ENGINE.begin() as conn:
        conn.execute(text("TRUNCATE candidate_lz, flood_extent RESTART IDENTITY"))
        if not lz.empty:
            for _, r in lz.iterrows():
                conn.execute(text(
                    "INSERT INTO candidate_lz (code, area_m2, mean_slope, landcover, score, geom, pad) "
                    "VALUES (:code, :area, :slope, :lc, :score, ST_GeomFromText(:g, 32736), ST_GeomFromText(:p, 32736))"),
                    dict(code=r.code, area=float(r.area_m2), slope=float(r.mean_slope), lc=r.landcover,
                         score=float(r["score"]), g=r["geometry"].wkt, p=r["pad"].wkt))
        for _, r in floods.iterrows():
            conn.execute(text("INSERT INTO flood_extent (level_m, area_m2, geom) "
                              "VALUES (:l, :a, ST_GeomFromText(:g, 32736))"),
                         dict(l=r.level_m, a=float(r.area_m2), g=r.geometry.wkt))
        conn.execute(text("ANALYZE candidate_lz; ANALYZE flood_extent;"))
        if conn.execute(text("SELECT to_regclass('flood_extent_parts')")).scalar():
            conn.execute(text("REFRESH MATERIALIZED VIEW flood_extent_parts; ANALYZE flood_extent_parts;"))

    log("5. Hillshade + GeoPackage ...")
    hillshade_png()
    gpkg = PROCESSED / "sentinel_shield.gpkg"
    if gpkg.exists():
        gpkg.unlink()
    for table in ["district_boundary", "study_area", "rivers", "roads", "villages", "buildings",
                  "power_lines", "staging_base", "flood_extent"]:
        g = read_table(table)
        if not g.empty:
            g.to_file(gpkg, layer=table, driver="GPKG")
    c = read_table("candidate_lz")
    if not c.empty:
        c.drop(columns=["pad"]).to_file(gpkg, layer="candidate_lz", driver="GPKG")
        gpd.GeoDataFrame(c.drop(columns=["geom"]), geometry=gpd.GeoSeries.from_wkb(
            c["pad"].apply(lambda h: bytes.fromhex(h) if isinstance(h, str) else h.wkb), crs=UTM36S)
        ).to_file(gpkg, layer="candidate_lz_pads", driver="GPKG")
    vor = PROCESSED / "village_voronoi.geojson"
    if vor.exists():
        gpd.read_file(vor).to_crs(UTM36S).to_file(gpkg, layer="village_voronoi", driver="GPKG")
    stats = {**lz_stats, "flood_levels": len(floods),
             "flood_km2": {str(r.level_m): round(r.area_m2 / 1e6, 3) for _, r in floods.iterrows()}}
    (PROCESSED / "terrain_stats.json").write_text(json.dumps(stats, indent=2))
    log(f"Done. {json.dumps(stats)}")


if __name__ == "__main__":
    main()
