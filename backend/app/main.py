"""Sentinel Shield API (FastAPI + psycopg).

Run:  .venv\\Scripts\\python -m uvicorn app.main:app --app-dir backend --port 8000
Docs: http://localhost:8000/docs

The heavy lifting is in PostGIS (db/functions.sql). The API only calls the SQL functions and
passes the JSON they build straight through, so there is no re-serialisation in Python.
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from psycopg_pool import ConnectionPool

from .settings import CONFIG, CONNINFO, CORS_ORIGINS, RESCUE, TERRAIN

pool = ConnectionPool(CONNINFO, min_size=1, max_size=4, open=False)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    pool.open(wait=True, timeout=10)
    yield
    pool.close()


app = FastAPI(title="Sentinel Shield API", version="1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["GET"],
                   allow_headers=["*"])


def query_one(sql: str, params: tuple = ()):
    with pool.connection() as conn:
        return conn.execute(sql, params).fetchone()


def json_text(text: str) -> Response:
    return Response(content=text, media_type="application/json")


@lru_cache(maxsize=1)
def levels() -> tuple[float, ...]:
    rows = None
    with pool.connection() as conn:
        rows = conn.execute("SELECT level_m FROM flood_extent ORDER BY level_m").fetchall()
    return tuple(float(r[0]) for r in rows)


@app.get("/api/health")
def health():
    t0 = time.perf_counter()
    try:
        version, n_levels, n_lz, n_vil = query_one(
            "SELECT postgis_lib_version(), (SELECT count(*) FROM flood_extent), "
            "(SELECT count(*) FROM candidate_lz), (SELECT count(*) FROM villages)")
    except Exception as ex:  # database down or not loaded
        raise HTTPException(503, f"database unavailable: {type(ex).__name__}")
    return {"status": "ok", "postgis": version, "flood_levels": n_levels, "candidate_lz": n_lz,
            "villages": n_vil, "db_ms": round((time.perf_counter() - t0) * 1000, 1)}


@lru_cache(maxsize=1)
def _meta() -> dict:
    w, s, e, n = query_one(
        "SELECT ST_XMin(b), ST_YMin(b), ST_XMax(b), ST_YMax(b) "
        "FROM (SELECT ST_Extent(ST_Transform(geom, 4326))::geometry AS b FROM study_area) x")
    base = query_one("SELECT name, ST_Y(ST_Transform(geom,4326)), ST_X(ST_Transform(geom,4326)) "
                     "FROM staging_base ORDER BY id LIMIT 1")
    lc_names = {30: "grassland", 40: "cropland", 60: "bare ground"}
    criteria = [
        f"Slope ≤ {TERRAIN['max_slope_deg']}°",
        "Open ground: " + ", ".join(lc_names.get(c, str(c)) for c in TERRAIN["open_landcover"]),
        f"≥ {RESCUE['min_lz_side_m']} × {RESCUE['min_lz_side_m']} m clear",
        f"≥ {TERRAIN['building_clearance_m']} m from buildings",
        f"≥ {TERRAIN['powerline_clearance_m']} m from power lines",
        f"≥ {RESCUE['water_clearance_m']} m from flood water",
        f"Village reach ≤ {RESCUE['max_lz_distance_m'] / 1000:g} km",
    ]
    return {
        "study_area": {"name": CONFIG["study_area"]["name"], "bbox": [w, s, e, n]},
        "levels": list(levels()),
        "staging_base": {"name": base[0], "lat": base[1], "lon": base[2]} if base else None,
        "assumptions": {
            "helicopter_speed_kmh": RESCUE["helicopter_speed_kmh"],
            "max_lz_distance_m": RESCUE["max_lz_distance_m"],
            "water_clearance_m": RESCUE["water_clearance_m"],
            "min_lz_side_m": RESCUE["min_lz_side_m"],
            "persons_per_building": CONFIG["population"]["persons_per_building"],
            **{k: TERRAIN[k] for k in ("max_slope_deg", "open_landcover", "building_clearance_m",
                                       "powerline_clearance_m")},
        },
        "criteria": criteria,
    }


@app.get("/api/meta")
def meta():
    return _meta()


@app.get("/api/scenario/{level}")
def scenario(level: float):
    lvl = round(level * 2) / 2          # snap to the 0.5 m steps
    if lvl not in levels():
        raise HTTPException(404, f"no flood level {level}; available: {list(levels())}")
    t0 = time.perf_counter()
    (doc,) = query_one("SELECT sentinel_scenario(%s::numeric, %s, %s, %s)::text",
                       (lvl, RESCUE["water_clearance_m"], RESCUE["max_lz_distance_m"],
                        RESCUE["helicopter_speed_kmh"]))
    ms = (time.perf_counter() - t0) * 1000
    resp = json_text(doc)
    resp.headers["X-Query-Ms"] = f"{ms:.1f}"
    resp.headers["Access-Control-Expose-Headers"] = "X-Query-Ms"
    return resp


@lru_cache(maxsize=1)
def _base_layers() -> str:
    (doc,) = query_one("SELECT sentinel_base_layers()::text")
    return doc


@app.get("/api/base-layers")
def base_layers():
    return json_text(_base_layers())
