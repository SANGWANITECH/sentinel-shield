"""Settings: database credentials from .env, modelling assumptions from config.toml."""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read_env() -> dict:
    env = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    # Real environment variables win (useful on another machine).
    for k in ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


ENV = _read_env()
with open(ROOT / "config.toml", "rb") as f:
    CONFIG = tomllib.load(f)

CONNINFO = (f"host={ENV.get('DB_HOST', 'localhost')} port={ENV.get('DB_PORT', '5432')} "
            f"dbname={ENV.get('DB_NAME', 'sentinel_shield')} user={ENV.get('DB_USER', 'postgres')} "
            f"password={ENV.get('DB_PASSWORD', '')}")

RESCUE = CONFIG["rescue"]
TERRAIN = CONFIG["terrain"]

CORS_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173",
                "http://localhost:4173", "http://127.0.0.1:4173"]
