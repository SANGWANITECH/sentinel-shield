"""API tests. They hit the real local database, so load the data first.

    .venv\\Scripts\\python -m pytest backend/tests -q
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:   # "with" runs the startup that opens the DB pool
        yield c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["flood_levels"] > 0 and body["villages"] > 0


def test_meta(client):
    m = client.get("/api/meta").json()
    w, s, e, n = m["study_area"]["bbox"]
    assert w < e and s < n and 33 < w < 34 and -14 < s < -13
    assert m["levels"] == sorted(m["levels"]) and 6.0 in m["levels"]
    assert m["assumptions"]["helicopter_speed_kmh"] == 180
    assert len(m["criteria"]) >= 5


def test_scenario_shape_and_totals(client):
    r = client.get("/api/scenario/3.0")
    assert r.status_code == 200
    d = r.json()
    for key in ("flood", "safe_lz", "lost_lz", "stranded", "links", "roads_cut", "totals"):
        assert key in d
    t = d["totals"]
    assert t["villages_stranded"] == t["reachable"] + t["no_safe_zone"]
    assert t["villages_stranded"] == len(d["stranded"]["features"])
    assert t["reachable"] == len(d["links"]["features"])
    assert t["safe_lz"] + t["lost_lz"] > 0
    for f in d["stranded"]["features"]:
        p = f["properties"]
        if p["status"] == "reachable":
            assert p["dist_m"] <= 1300 and p["eta_min"] > 0


def test_flood_grows_with_level(client):
    low = client.get("/api/scenario/1.0").json()["totals"]
    high = client.get("/api/scenario/6.0").json()["totals"]
    assert high["flood_area_km2"] >= low["flood_area_km2"]
    assert high["safe_lz"] <= low["safe_lz"]


def test_coordinates_are_wgs84(client):
    d = client.get("/api/scenario/6.0").json()
    lon, lat = d["flood"]["features"][0]["geometry"]["coordinates"][0][0][0]
    assert 33 < lon < 34 and -14 < lat < -13


def test_unknown_level_404(client):
    assert client.get("/api/scenario/9.5").status_code == 404


def test_base_layers(client):
    b = client.get("/api/base-layers").json()
    for key in ("district", "study_area", "rivers", "roads", "villages"):
        assert b[key]["type"] == "FeatureCollection"
    assert len(b["villages"]["features"]) > 0
