"""Detection API: runs against stored observations, persists, replaces."""

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
    return TestClient(app, headers={"X-Requested-With": "XMLHttpRequest"})


def _rules(report: dict, icao24: str | None = None) -> set[str]:
    return {
        d["rule"]
        for d in report["detections"]
        if icao24 is None or d["icao24"] == icao24
    }


def test_detect_demo_reports_teleport_altitude_and_correlated_event(tmp_path):
    client = _client(tmp_path)
    response = client.post("/api/detect")
    assert response.status_code == 200
    report = response.json()
    assert report["observations_analysed"] == 33
    assert report["aircraft_analysed"] == 6

    # The jump track and all three common-offset aircraft teleport.
    teleport_aircraft = {
        d["icao24"] for d in report["detections"] if d["rule"] == "teleport"
    }
    assert {"def456", "111aaa", "222bbb", "333ccc"} <= teleport_aircraft
    # Altitude divergence appears at medium and high severity.
    altitude = [
        d for d in report["detections"] if d["rule"] == "altitude_divergence"
    ]
    assert {d["severity"] for d in altitude} == {"medium", "high"}
    # The normal track has no movement anomalies (only data-quality
    # findings for its missing-coordinate record and the gap to it).
    assert not (_rules(report, "abc123") & {
        "teleport", "implausible_acceleration", "sharp_heading_change",
        "course_discontinuity", "altitude_divergence",
    })
    # Every detection is traceable to supporting observations.
    assert all(d["supporting_observation_ids"] for d in report["detections"])

    # The common-offset group becomes one correlated event.
    correlated = [e for e in report["events"] if e["kind"] == "correlated_anomalies"]
    assert len(correlated) == 1
    assert correlated[0]["aircraft"] == ["111aaa", "222bbb", "333ccc"]
    assert "not confirmation" in correlated[0]["caveat"]
    assert correlated[0]["member_anomaly_ids"]
    # The jump track becomes a single-aircraft event.
    assert any(
        e["kind"] == "single_aircraft_anomaly" and e["aircraft"] == ["def456"]
        for e in report["events"]
    )
    # No event is ever labelled verified by a detection run.
    assert all(e["status"] == "candidate" for e in report["events"])


def test_detect_replaces_previous_results_and_stored_reads_match(tmp_path):
    client = _client(tmp_path)
    first = client.post("/api/detect").json()
    second = client.post("/api/detect").json()
    assert len(second["detections"]) == len(first["detections"])
    assert len(second["events"]) == len(first["events"])

    anomalies = client.get("/api/anomalies", params={"rule": "teleport"}).json()
    assert anomalies
    assert all(a["rule"] == "teleport" for a in anomalies)
    assert all(a["supporting_observation_ids"] for a in anomalies)

    events = client.get("/api/events").json()
    assert events
    assert all(e["member_anomaly_ids"] for e in events)

    # Filtering the run to one aircraft analyses only that aircraft.
    filtered = client.post("/api/detect", params={"icao24": "def456"}).json()
    assert filtered["aircraft_analysed"] == 1
    assert all(d["icao24"] == "def456" for d in filtered["detections"])


def test_detect_on_empty_database_returns_empty_report(tmp_path):
    db_path = tmp_path / "empty.db"
    init_db(db_path)
    app = create_app(Settings(database_path=db_path))
    client = TestClient(app, headers={"X-Requested-With": "XMLHttpRequest"})
    report = client.post("/api/detect").json()
    assert report["detections"] == []
    assert report["events"] == []
    assert report["summary"]["detections_total"] == 0
