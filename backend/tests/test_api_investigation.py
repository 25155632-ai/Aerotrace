"""Investigation API: filters, sorting, pagination, ids, export."""

import csv
import io

from fastapi.testclient import TestClient

from app.config import Settings
from app.ingestion.demo_data import demo_observations
from app.ingestion.pipeline import ingest_observations
from app.main import create_app
from app.store import get_connection, init_db


def _client(tmp_path) -> TestClient:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    with get_connection(db_path) as connection:
        ingest_observations(connection, demo_observations())
    app = create_app(Settings(database_path=db_path))
    client = TestClient(app, headers={"X-Requested-With": "XMLHttpRequest"})
    assert client.post("/api/detect").status_code == 200
    return client


def test_anomaly_filters_return_correct_records(tmp_path):
    client = _client(tmp_path)
    # Rule filter.
    teleport = client.get("/api/anomalies", params={"rule": "teleport"}).json()
    assert teleport
    assert all(a["rule"] == "teleport" for a in teleport)
    assert {a["icao24"] for a in teleport} == {"def456", "111aaa", "222bbb", "333ccc"}
    # Severity filter.
    high = client.get("/api/anomalies", params={"severity": "high"}).json()
    assert high and all(a["severity"] == "high" for a in high)
    # Category (kind) filter: data-quality findings only.
    quality = client.get("/api/anomalies", params={"kind": "data_quality"}).json()
    assert quality
    assert all(a["kind"] == "data_quality" for a in quality)
    assert {a["rule"] for a in quality} == {"missing_position", "track_gap"}
    # Aircraft filter.
    jump = client.get("/api/anomalies", params={"icao24": "def456"}).json()
    assert jump and all(a["icao24"] == "def456" for a in jump)
    # Time filter: only detections at the demo base time window's end.
    # All demo detections are at BASE+20..BASE+1000; a range that ends
    # before BASE+20 must return nothing.
    none = client.get(
        "/api/anomalies", params={"from_ts": 1700000000, "to_ts": 1700000019}
    ).json()
    assert none == []
    late = client.get(
        "/api/anomalies", params={"from_ts": 1700001000, "to_ts": 1700001000}
    ).json()
    assert late and all(a["detected_at"] == 1700001000 for a in late)
    # Location filter: a box around the common-offset group only.
    boxed = client.get(
        "/api/anomalies",
        params={"min_lat": 20.0, "max_lat": 21.0, "min_lon": 74.0, "max_lon": 75.0},
    ).json()
    assert boxed
    assert {a["icao24"] for a in boxed} <= {"111aaa", "222bbb", "333ccc"}
    assert all(
        20.0 <= a["latitude"] <= 21.0 and 74.0 <= a["longitude"] <= 75.0
        for a in boxed
    )
    # Invalid ranges are rejected, not silently ignored.
    assert client.get(
        "/api/anomalies", params={"from_ts": 200, "to_ts": 100}
    ).status_code == 422
    assert client.get(
        "/api/anomalies", params={"min_lat": 30, "max_lat": 20}
    ).status_code == 422


def test_anomaly_sorting_and_pagination(tmp_path):
    client = _client(tmp_path)
    all_rows = client.get("/api/anomalies", params={"limit": 100}).json()
    total = len(all_rows)
    assert total > 4
    # Default sort is newest first.
    times = [a["detected_at"] for a in all_rows]
    assert times == sorted(times, reverse=True)
    # Oldest first reverses it.
    oldest = client.get(
        "/api/anomalies", params={"sort": "detected_at_asc", "limit": 100}
    ).json()
    assert [a["id"] for a in oldest] == [a["id"] for a in reversed(all_rows)]
    # Pagination pages do not overlap and together cover all rows.
    page1 = client.get("/api/anomalies", params={"limit": 2, "offset": 0}).json()
    page2 = client.get("/api/anomalies", params={"limit": 2, "offset": 2}).json()
    assert len(page1) == 2 and len(page2) == 2
    assert {a["id"] for a in page1}.isdisjoint({a["id"] for a in page2})
    assert [a["id"] for a in page1] == [a["id"] for a in all_rows[:2]]
    assert [a["id"] for a in page2] == [a["id"] for a in all_rows[2:4]]


def test_observations_by_ids_and_anomalies_by_ids(tmp_path):
    client = _client(tmp_path)
    teleport = client.get("/api/anomalies", params={"rule": "teleport"}).json()
    supporting = teleport[0]["supporting_observation_ids"]
    assert supporting
    observations = client.get(
        "/api/observations",
        params=[("ids", i) for i in supporting],
    ).json()
    assert [o["id"] for o in observations] == supporting
    # Anomalies by ids: event members resolve to their rows.
    events = client.get("/api/events").json()
    correlated = next(e for e in events if e["kind"] == "correlated_anomalies")
    member_ids = correlated["member_anomaly_ids"]
    members = client.get(
        "/api/anomalies",
        params=[("ids", i) for i in member_ids] + [("limit", 100)],
    ).json()
    assert {m["id"] for m in members} == set(member_ids)


def test_event_time_filter_and_export(tmp_path):
    client = _client(tmp_path)
    # Events overlapping a window that ends before any event starts:
    # none. (Demo events start at BASE+20.)
    none = client.get(
        "/api/events", params={"from_ts": 1700000000, "to_ts": 1700000019}
    ).json()
    assert none == []
    some = client.get("/api/events", params={"to_ts": 1700000040}).json()
    assert some

    # Anomaly export honours the same filters as the list endpoint.
    response = client.get("/api/anomalies/export", params={"rule": "teleport"})
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert rows
    assert all(r["rule"] == "teleport" for r in rows)
    assert all(r["supporting_observation_ids"] for r in rows)
    assert all(r["detected_at_utc"].endswith(" UTC") for r in rows)
    # Unfiltered export contains every detection.
    all_rows = list(
        csv.DictReader(io.StringIO(client.get("/api/anomalies/export").text))
    )
    listed = client.get("/api/anomalies", params={"limit": 100}).json()
    assert len(all_rows) == len(listed)

    # Event export contains the correlated event with its aircraft.
    event_rows = list(
        csv.DictReader(
            io.StringIO(
                client.get(
                    "/api/events/export", params={"kind": "correlated_anomalies"}
                ).text
            )
        )
    )
    assert len(event_rows) == 1
    assert event_rows[0]["aircraft"] == "111aaa;222bbb;333ccc"
    assert event_rows[0]["status"] == "candidate"
