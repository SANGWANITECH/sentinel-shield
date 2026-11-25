"""Shared helpers: project paths, config.toml, .env database settings."""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"

WGS84 = "EPSG:4326"
UTM36S = "EPSG:32736"   # storage CRS: metres


def load_config() -> dict:
    with open(ROOT / "config.toml", "rb") as f:
        return tomllib.load(f)


def load_env() -> dict:
    """Read KEY=VALUE lines from .env (no extra dependency needed)."""
    env = {}
    path = ROOT / ".env"
    if not path.exists():
        raise SystemExit(".env not found - copy .env.example to .env and set DB_PASSWORD")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def db_url() -> str:
    e = load_env()
    return (f"postgresql+psycopg://{e['DB_USER']}:{e['DB_PASSWORD']}"
            f"@{e['DB_HOST']}:{e['DB_PORT']}/{e['DB_NAME']}")


def pg_env() -> dict:
    """Environment for psql / raster2pgsql subprocesses."""
    e = load_env()
    env = os.environ.copy()
    env.update(PGHOST=e["DB_HOST"], PGPORT=e["DB_PORT"], PGDATABASE=e["DB_NAME"],
               PGUSER=e["DB_USER"], PGPASSWORD=e["DB_PASSWORD"])
    return env


def bbox(cfg: dict) -> tuple[float, float, float, float]:
    s = cfg["study_area"]
    return s["west"], s["south"], s["east"], s["north"]
