"""Ingestion API: demo load, live success, live failure stores nothing."""

from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

STATE = [
    "aabbcc", "LIVE1", "X", 1700000000, 1700000000, 72.87, 19.07, 10000.0,
    False, 250.0, 45.0, 0.0, None, 10050.0, None, False, 0, 0,
]


def _client(tmp_path: Path, transport=None) -> TestClient:
    app = create_app(Settings(database_path=tmp_path / "test.db"))
    app.state.opensky_transport = transport
    return TestClient(app, headers={"X-Requested-With": "XMLHttpRequest"})


def test_sources_report_demo_and_unverified_live_on_fresh_db(tmp_path: Path) -> None:
    """A fresh database has no recorded live fetch, so the live source
    reports not_verified — never a hardcoded verified claim."""
    with _client(tmp_path) as client:
        response = client.get("/api/sources")
    assert response.status_code == 200
    by_name = {item["name"]: item for item in response.json()}
    assert by_name["spoofsky-demo"]["status"] == "available_on_demand"
    assert by_name["spoofsky-demo"]["kind"] == "simulated"
    assert by_name["opensky-live"]["status"] == "not_verified"
    assert by_name["opensky-live"]["kind"] == "real"


def test_sources_follow_real_fetch_outcomes(tmp_path: Path) -> None:
    """A successful fetch flips the status to verified_live; a later
    failure flips it to fetch_failed. The status tracks attempts, not
    a one-time historical claim."""

    def ok_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"time": 1700000000, "states": [STATE]})

    def fail_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    with _client(tmp_path, transport=httpx.MockTransport(ok_handler)) as client:
        assert client.post("/api/ingest/opensky").status_code == 200
        by_name = {item["name"]: item for item in client.get("/api/sources").json()}
        assert by_name["opensky-live"]["status"] == "verified_live"
        assert "1 records stored" in by_name["opensky-live"]["note"]
    # A second client on the same database, now with a failing adapter.
    app = client.app
    app.state.opensky_transport = httpx.MockTransport(fail_handler)
    assert client.post("/api/ingest/opensky").status_code == 502
    by_name = {item["name"]: item for item in client.get("/api/sources").json()}
    assert by_name["opensky-live"]["status"] == "fetch_failed"


def test_demo_load_and_filtered_reads(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.post("/api/ingest/demo")
        assert response.status_code == 200
        report = response.json()
        assert (report["total"], report["stored"], report["duplicates"]) == (34, 33, 1)
        assert report["source"] == "spoofsky-demo"

        simulated = client.get("/api/observations", params={"source_kind": "simulated"})
        assert len(simulated.json()) == 33
        assert all(row["source_kind"] == "simulated" for row in simulated.json())

        real = client.get("/api/observations", params={"source_kind": "real"})
        assert real.json() == []

        track = client.get("/api/observations", params={"icao24": "abc123"})
        times = [row["observed_at"] for row in track.json()]
        assert times == sorted(times)

        second = client.post("/api/ingest/demo")
        assert second.json()["stored"] == 0
        assert second.json()["duplicates"] == 34


def test_opensky_success_stores_real_records_separately(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"time": 1700000000, "states": [STATE]})

    with _client(tmp_path, transport=httpx.MockTransport(handler)) as client:
        response = client.post("/api/ingest/opensky")
        assert response.status_code == 200
        report = response.json()
        assert report["source"] == "opensky-live"
        assert report["stored"] == 1
        assert report["snapshot_time"] == 1700000000
        real = client.get("/api/observations", params={"source_kind": "real"})
        assert len(real.json()) == 1
        assert real.json()[0]["source"] == "opensky-live"
        assert real.json()[0]["icao24"] == "aabbcc"


def test_opensky_failure_stores_nothing_and_substitutes_nothing(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    with _client(tmp_path, transport=httpx.MockTransport(handler)) as client:
        response = client.post("/api/ingest/opensky")
        assert response.status_code == 502
        assert response.json()["detail"]["error"] == "source_unavailable"
        # No demo data, no partial data: the failed fetch stored nothing.
        assert client.get("/api/observations").json() == []


def test_opensky_rate_limit_maps_to_429(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, headers={"X-Rate-Limit-Retry-After-Seconds": "0"}, json={}
        )

    with _client(tmp_path, transport=httpx.MockTransport(handler)) as client:
        response = client.post("/api/ingest/opensky")
    assert response.status_code == 429
    assert response.json()["detail"]["error"] == "rate_limited"


def test_invalid_bbox_and_source_kind_are_422(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        assert (
            client.post(
                "/api/ingest/opensky",
                json={"lamin": 50.0, "lamax": 40.0, "lomin": 0.0, "lomax": 10.0},
            ).status_code
            == 422
        )
        assert (
            client.get("/api/observations", params={"source_kind": "bogus"}).status_code
            == 422
        )
