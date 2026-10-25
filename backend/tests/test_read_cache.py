"""Read-cache honesty: a write must be visible on the next read, and a
cached list must never leak one database's rows into another's."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.cache import CACHE, ReadCache
from app.config import Settings
from app.main import create_app
from app.ingestion.pipeline import ingest_observations
from app.store import get_connection, init_db


def make_client(db_path: Path) -> TestClient:
    init_db(db_path)
    app = create_app(Settings(database_path=db_path))
    return TestClient(app, headers={"X-Requested-With": "XMLHttpRequest"})


def obs(icao24, ts, lat=10.0, lon=20.0, alt=9000.0):
    return {
        "icao24": icao24,
        "observed_at": ts,
        "latitude": lat,
        "longitude": lon,
        "baro_altitude": alt,
        "geo_altitude": alt,
        "velocity": 250.0,
        "heading": 90.0,
        "callsign": "TEST1",
        "source": "unit-test",
        "source_kind": "simulated",
    }


def seed(db_path: Path, rows):
    init_db(db_path)
    with get_connection(db_path) as conn:
        ingest_observations(conn, rows)


def test_cache_unit_bounded_and_expiring():
    cache = ReadCache(ttl_seconds=0, max_entries=2)
    cache.put("a", [1])
    assert cache.get("a") is None  # zero TTL expires immediately
    cache2 = ReadCache(ttl_seconds=60, max_entries=2)
    cache2.put("a", 1)
    cache2.put("b", 2)
    cache2.put("c", 3)
    assert len(cache2) == 2 and cache2.get("a") is None


def test_write_visible_after_cached_read(tmp_path):
    db = tmp_path / "cache.db"
    seed(db, [obs("aaa111", 1700000000)])
    CACHE.invalidate()
    client = make_client(db)
    first = client.get("/api/observations")
    assert len(first.json()) == 1
    # A second identical read may come from cache; then ingest more.
    second = client.get("/api/observations")
    assert len(second.json()) == 1
    report = client.post("/api/ingest/demo")
    assert report.status_code == 200
    after = client.get("/api/observations")
    assert len(after.json()) > 1  # write invalidation, not TTL expiry


def test_cache_scoped_by_database(tmp_path):
    db_a = tmp_path / "a.db"
    db_b = tmp_path / "b.db"
    seed(db_a, [obs("aaa111", 1700000000)])
    seed(db_b, [obs("bbb222", 1700000001), obs("ccc333", 1700000002)])
    CACHE.invalidate()
    client_a = make_client(db_a)
    client_b = make_client(db_b)
    assert len(client_a.get("/api/observations").json()) == 1
    assert len(client_b.get("/api/observations").json()) == 2
    assert len(client_a.get("/api/observations").json()) == 1
