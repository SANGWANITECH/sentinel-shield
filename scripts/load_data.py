"""Reproject data/raw to EPSG:32736, clip to the study area and load it into PostGIS.

    python scripts/load_data.py

Re-runnable: every vector table is emptied (TRUNCATE ... RESTART IDENTITY) and reloaded in a
single transaction, and the raster tables are dropped and recreated by raster2pgsql (-d).
Also estimates village population from building counts in Voronoi cells (see config.toml).
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import shapely
from rasterio.warp import Resampling, calculate_default_transform, reproject
from shapely.geometry import MultiLineString, MultiPolygon, Point, box
from sqlalchemy import create_engine, text

from common import PROCESSED, RAW, UTM36S, WGS84, bbox, db_url, load_config, pg_env

CFG = load_config()
VECTOR_TABLES = ["district_boundary", "study_area", "rivers", "roads", "villages", "buildings",
                 "power_lines", "staging_base"]


def log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
def as_multi(gdf: gpd.GeoDataFrame, kind: str) -> gpd.GeoDataFrame:
    """Split into single parts, drop wrong types (e.g. points left by clipping a line on the
    boundary) and wrap each part as Multi* to match the table's geometry type."""
    single, multi = {"polygon": ("Polygon", MultiPolygon), "line": ("LineString", MultiLineString)}[kind]
    gdf = gdf.explode(index_parts=False)
    gdf = gdf[gdf.geom_type == single].copy()
    gdf["geometry"] = [multi([g]) for g in gdf.geometry]
    return gdf


def read_clip(name: str, study: gpd.GeoDataFrame, kind: str) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(RAW / f"osm_{name}.geojson").to_crs(UTM36S)
    gdf["geometry"] = gdf.geometry.make_valid()
    gdf = gpd.clip(gdf, study)
    return gdf if kind == "point" else as_multi(gdf, kind)


def to_table(gdf: gpd.GeoDataFrame, table: str, cols: list[str], conn) -> None:
    if gdf.empty:
        return log(f"  {table:<18}      0 rows (no features in study area)")
    out = gdf[cols + ["geometry"]].rename_geometry("geom")
    out.to_postgis(table, conn, if_exists="append", index=False)
    log(f"  {table:<18} {len(out):>6} rows")


# ---------------------------------------------------------------------------
# Settlements from building clusters (used when OSM has too few village nodes)
# ---------------------------------------------------------------------------
NAME_SUFFIX = re.compile(
    r"\b(community day secondary school|cdss|secondary school|primary school|full primary school|"
    r"junior primary school|school|ccap|church|mosque|parish|health cent(re|er)|clinic|hospital|"
    r"market(place)?|f\.?p\.?s\.?|j\.?p\.?s\.?|p\.?s\.?)\b", re.I)


def clean_name(name: str) -> str:
    n = NAME_SUFFIX.sub("", name or "")
    n = re.sub(r"\s+", " ", n).strip(" -,.")
    return n


def derive_settlements(buildings: gpd.GeoDataFrame, named: gpd.GeoDataFrame,
                       study: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    sc = CFG["settlements"]
    cent = gpd.GeoDataFrame(geometry=buildings.geometry.representative_point(), crs=UTM36S)
    blobs = shapely.union_all(cent.buffer(sc["link_m"] / 2).values)
    clusters = gpd.GeoDataFrame(geometry=list(shapely.get_parts(blobs)), crs=UTM36S)
    cent = gpd.sjoin(cent, clusters, predicate="within").rename(columns={"index_right": "cid"})
    groups = []
    for cid, g in cent.groupby("cid"):
        if len(g) < sc["min_buildings"]:
            continue
        if len(g) > sc["max_cluster_buildings"]:          # a town: split into sectors
            cell = sc["split_cell_m"]
            keys = (np.floor(g.geometry.x / cell).astype(int).astype(str) + "_" +
                    np.floor(g.geometry.y / cell).astype(int).astype(str))
            for _, sub in g.groupby(keys):
                if len(sub) >= sc["min_buildings"]:
                    groups.append(sub)
        else:
            groups.append(g)
    rows = []
    for g in groups:
        mx, my = g.geometry.x.mean(), g.geometry.y.mean()
        # Snap to the member building nearest the cluster centre so the point sits in the village.
        d = np.hypot(g.geometry.x - mx, g.geometry.y - my)
        fp = shapely.union_all(g.buffer(sc["link_m"] / 2).values)
        rows.append({"n_bld": len(g), "geometry": g.geometry.iloc[int(np.argmin(d.values))],
                     "footprint": fp})
    st = gpd.GeoDataFrame(rows, geometry="geometry", crs=UTM36S)
    st = gpd.clip(st, study)

    # Names: nearest named feature (places first, then residential areas, then amenities).
    named = named.copy()
    named["clean"] = named["name"].map(clean_name)
    named = named[named["clean"].str.len() > 1]
    named["rank"] = np.where(named["place"].notna(), 0, np.where(named["landuse"].notna(), 1, 2))
    names = []
    for p in st.geometry:
        best = None
        if not named.empty:
            d = named.distance(p)
            cand = named[d <= sc["name_search_m"]].assign(d=d)
            if not cand.empty:
                best = cand.sort_values(["rank", "d"]).iloc[0]["clean"]
        names.append(best)
    st["name"] = names
    # Unnamed or repeated names get a number so every settlement is distinguishable.
    counter: dict[str, int] = {}
    out_names = []
    for n in st["name"]:
        if not isinstance(n, str) or not n:      # None / NaN
            out_names.append(None)
            continue
        counter[n] = counter.get(n, 0) + 1
        out_names.append(n if counter[n] == 1 else f"{n} {counter[n]}")
    st["name"] = pd.Series(out_names, index=st.index, dtype=object)
    k = 0
    for i in st.index:
        if not isinstance(st.at[i, "name"], str):
            k += 1
            st.at[i, "name"] = f"Settlement {k:02d}"
    return st.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Population estimate
# ---------------------------------------------------------------------------
def estimate_population(villages: gpd.GeoDataFrame, buildings: gpd.GeoDataFrame,
                        study: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Voronoi cell per village; population = buildings in cell x persons_per_building."""
    ppb = CFG["population"]["persons_per_building"]
    area = study.geometry.iloc[0]
    if len(villages) == 1:
        cells = gpd.GeoDataFrame(geometry=[area], crs=UTM36S)
    else:
        vor = shapely.voronoi_polygons(shapely.MultiPoint(list(villages.geometry)), extend_to=area)
        cells = gpd.GeoDataFrame(geometry=list(vor.geoms), crs=UTM36S).clip(study)
    # Voronoi output order is arbitrary, so match each cell to the village inside it.
    cells = gpd.sjoin(cells, villages[["geometry"]], predicate="contains").rename(
        columns={"index_right": "vid"})
    centroids = gpd.GeoDataFrame(geometry=buildings.geometry.representative_point(), crs=UTM36S)
    counts = gpd.sjoin(centroids, cells[["vid", "geometry"]], predicate="within").groupby("vid").size()
    villages = villages.copy()
    villages["building_count"] = counts.reindex(villages.index).fillna(0).astype(int)
    villages["population"] = np.round(villages["building_count"] * ppb).astype(int)
    cells = cells.merge(villages[["name", "building_count", "population"]], left_on="vid",
                        right_index=True)
    PROCESSED.mkdir(parents=True, exist_ok=True)
    cells.to_crs(WGS84).to_file(PROCESSED / "village_voronoi.geojson", driver="GeoJSON")
    return villages


# ---------------------------------------------------------------------------
# Rasters
# ---------------------------------------------------------------------------
def reproject_raster(src_name: str, dst_name: str, res: float, resampling, nodata) -> str:
    src_path, dst_path = RAW / f"{src_name}.tif", PROCESSED / f"{dst_name}.tif"
    with rasterio.open(src_path) as src:
        transform, w, h = calculate_default_transform(src.crs, UTM36S, src.width, src.height,
                                                      *src.bounds, resolution=res)
        prof = src.profile.copy()
        prof.update(crs=UTM36S, transform=transform, width=w, height=h, nodata=nodata,
                    compress="deflate", tiled=True, blockxsize=256, blockysize=256)
        with rasterio.open(dst_path, "w", **prof) as dst:
            reproject(rasterio.band(src, 1), rasterio.band(dst, 1), dst_crs=UTM36S,
                      dst_transform=transform, resampling=resampling,
                      src_nodata=src.nodata, dst_nodata=nodata)
    log(f"  {dst_path.name}: {w}x{h} px at {res} m")
    return str(dst_path)


def load_raster(path: str, table: str, comment: str) -> None:
    r2p, psql = shutil.which("raster2pgsql"), shutil.which("psql")
    if not (r2p and psql):
        raise SystemExit("raster2pgsql/psql not on PATH (PostgreSQL bin folder)")
    env = pg_env()
    # Point PostgreSQL's own GDAL at its own PROJ database (avoids "Cannot find proj.db").
    for k in ("PROJ_LIB", "PROJ_DATA"):
        env.pop(k, None)
    proj = sorted(Path(r2p).resolve().parents[1].glob("share/contrib/postgis-*/proj"))
    if proj:
        env["PROJ_LIB"] = env["PROJ_DATA"] = str(proj[-1])
    # -d drop+recreate (re-runnable), -I GIST index, -C constraints, -M analyze, -t tile size
    p1 = subprocess.Popen([r2p, "-s", "32736", "-d", "-I", "-C", "-M", "-t", "100x100", path, table],
                          stdout=subprocess.PIPE, env=env)
    p2 = subprocess.run([psql, "-q", "-v", "ON_ERROR_STOP=1"], stdin=p1.stdout, env=env,
                        capture_output=True, text=True)
    p1.stdout.close()
    if p1.wait() or p2.returncode:
        raise SystemExit(f"raster load failed for {table}: {p2.stderr}")
    subprocess.run([psql, "-q", "-c", f"COMMENT ON TABLE {table} IS '{comment}';"], env=env, check=True)
    log(f"  {table:<18} loaded (raster)")


# ---------------------------------------------------------------------------
def main() -> None:
    sa = CFG["study_area"]
    study = gpd.GeoDataFrame({"name": [sa["name"]], "description": [sa["description"]]},
                             geometry=[box(*bbox(CFG))], crs=WGS84).to_crs(UTM36S)
    study = as_multi(study, "polygon")

    log("Preparing vector layers ...")
    district = as_multi(gpd.read_file(RAW / "dowa_boundary.geojson").to_crs(UTM36S), "polygon")
    rivers = read_clip("rivers", study, "line")
    roads = read_clip("roads", study, "line")
    buildings = read_clip("buildings", study, "polygon")
    buildings["building"] = "osm"      # column records the SOURCE (OSM tags not downloaded)
    ms_path = RAW / "ms_buildings.geojson"
    if ms_path.exists():
        # Microsoft ML footprints fill the (large) gaps in OSM; drop those overlapping OSM ones.
        ms = gpd.read_file(ms_path).to_crs(UTM36S)
        ms["geometry"] = ms.geometry.make_valid()
        ms = as_multi(gpd.clip(ms, study), "polygon")
        hit = gpd.sjoin(ms, buildings[["geometry"]], predicate="intersects", how="inner").index.unique()
        ms = ms.drop(index=hit)
        ms["building"] = "microsoft"
        log(f"  buildings: {len(buildings)} OSM + {len(ms)} Microsoft (non-overlapping)")
        buildings = pd.concat([buildings[["building", "geometry"]], ms[["building", "geometry"]]],
                              ignore_index=True)
        buildings = gpd.GeoDataFrame(buildings, geometry="geometry", crs=UTM36S)
    power = read_clip("power_lines", study, "line")

    villages = read_clip("places", study, "point")
    villages = villages[villages.geom_type == "Point"].drop_duplicates(subset="geometry")
    villages["name"] = villages["name"].fillna("").replace("", np.nan)
    villages["name"] = villages["name"].fillna(pd.Series(
        [f"Unnamed place {i+1}" for i in range(len(villages))], index=villages.index))
    villages = villages.reset_index(drop=True)
    if len(villages) < CFG["settlements"]["min_osm_villages"]:
        log(f"  only {len(villages)} OSM village nodes -> deriving settlements from building clusters")
        named_path = RAW / "osm_named_features.geojson"
        named = (gpd.read_file(named_path).to_crs(UTM36S) if named_path.exists()
                 else gpd.GeoDataFrame(columns=["name", "place", "amenity", "landuse", "geometry"],
                                       geometry="geometry", crs=UTM36S))
        # OSM place nodes also serve as names (e.g. Mponela for the town sectors).
        extra = villages.assign(amenity=None, landuse=None)[["name", "place", "amenity", "landuse", "geometry"]]
        named = pd.concat([named[["name", "place", "amenity", "landuse", "geometry"]], extra], ignore_index=True)
        villages = derive_settlements(buildings, gpd.GeoDataFrame(named, geometry="geometry", crs=UTM36S), study)
        log(f"  derived {len(villages)} settlements")
    if "footprint" not in villages.columns:          # OSM village nodes: assume a 150 m radius
        villages["footprint"] = villages.geometry.buffer(150)
    villages = estimate_population(villages, buildings, study)
    # Second geometry column goes in as EWKT text (PostGIS parses it; SRID must match the column).
    villages["footprint"] = [
        None if g is None else "SRID=32736;" + (g if g.geom_type == "MultiPolygon"
                                                else MultiPolygon(list(shapely.get_parts(g)))).wkt
        for g in villages["footprint"]]

    r = CFG["rescue"]
    staging = gpd.GeoDataFrame(
        {"name": [r["staging_base_name"]],
         "description": [f"Helicopter staging base; cruise speed assumption {r['helicopter_speed_kmh']} km/h"]},
        geometry=[Point(r["staging_base_lon"], r["staging_base_lat"])], crs=WGS84).to_crs(UTM36S)

    log("Loading vector tables (one transaction) ...")
    engine = create_engine(db_url())
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(VECTOR_TABLES)} RESTART IDENTITY"))
        to_table(district, "district_boundary", ["name"], conn)
        to_table(study, "study_area", ["name", "description"], conn)
        to_table(rivers, "rivers", ["name", "waterway"], conn)
        to_table(roads, "roads", ["name", "highway"], conn)
        to_table(villages, "villages", ["name", "population", "footprint"], conn)
        to_table(buildings, "buildings", ["building"], conn)
        to_table(power, "power_lines", ["voltage"], conn)
        to_table(staging, "staging_base", ["name", "description"], conn)

    log("Reprojecting and loading rasters ...")
    dem = reproject_raster("dem_cop30", "dem_32736", 30, Resampling.bilinear, -9999.0)
    lc = reproject_raster("worldcover_2021", "landcover_32736", 10, Resampling.nearest, 0)
    load_raster(dem, "dem", "Copernicus GLO-30 elevation (m), study area + margin, EPSG:32736. Input for flood simulation and slope.")
    load_raster(lc, "landcover", "ESA WorldCover 2021 10 m classes, study area + margin, EPSG:32736. Input for land-cover reclassification.")
    log("Done.")


if __name__ == "__main__":
    main()
