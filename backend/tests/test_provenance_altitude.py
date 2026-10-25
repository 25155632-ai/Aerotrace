"""Provenance labels (audit fix) and stale altitude-divergence eligibility.

Provenance is decided by a detection's supporting observations, not by
its whole track: a teleport built from one real and one simulated
point is 'mixed', not 'simulated'. Altitude divergence is a per-point
detector: a stale flag or a missing position must not suppress it,
while genuinely invalid altitudes are rejected, never coerced.
"""

from app.detectors.detectors import run_track_detectors


def _point(pid, at, lat, lon, kind, **extra):
    point = {
        "id": pid,
        "icao24": "abcdef",
        "observed_at": at,
        "latitude": lat,
        "longitude": lon,
        "source_kind": kind,
        "quality_note": None,
    }
    point.update(extra)
    return point


def _teleport(kind_a, kind_b):
    return run_track_detectors(
        [
            _point(1, 1700000000, 19.0, 72.0, kind_a),
            _point(2, 1700000010, 20.3, 72.0, kind_b),
        ]
    )


def test_mixed_teleport_is_labelled_mixed_with_contributing_kinds():
    detections = [d for d in _teleport("real", "simulated") if d.rule == "teleport"]
    assert len(detections) == 1
    assert detections[0].source_kind == "mixed"
    assert detections[0].source_kinds == ["real", "simulated"]


def test_homogeneous_teleport_keeps_its_kind():
    real = [d for d in _teleport("real", "real") if d.rule == "teleport"]
    assert real[0].source_kind == "real"
    assert real[0].source_kinds == ["real"]
    sim = [d for d in _teleport("simulated", "simulated") if d.rule == "teleport"]
    assert sim[0].source_kind == "simulated"
    assert sim[0].source_kinds == ["simulated"]


def test_missing_provenance_never_claims_real():
    detections = run_track_detectors(
        [
            {"id": 1, "icao24": "abcdef", "observed_at": 1700000000, "latitude": 19.0, "longitude": 72.0},
            {"id": 2, "icao24": "abcdef", "observed_at": 1700000010, "latitude": 20.3, "longitude": 72.0},
        ]
    )
    teleport = [d for d in detections if d.rule == "teleport"]
    assert teleport[0].source_kind == "simulated"
    assert teleport[0].source_kinds == []


def test_stale_altitude_divergence_fires_with_stale_context():
    detections = run_track_detectors(
        [
            _point(
                1,
                1700000000,
                19.0,
                72.0,
                "simulated",
                baro_altitude_m=10000,
                geo_altitude_m=12000,
                quality_note="stale",
            )
        ]
    )
    altitude = [d for d in detections if d.rule == "altitude_divergence"]
    assert len(altitude) == 1
    assert altitude[0].evidence["divergence_m"] == 2000.0
    assert altitude[0].evidence["quality_note"] == "stale"


def test_fresh_altitude_divergence_still_fires_without_quality_note():
    detections = run_track_detectors(
        [
            _point(
                1,
                1700000000,
                19.0,
                72.0,
                "simulated",
                baro_altitude_m=10000,
                geo_altitude_m=12000,
            )
        ]
    )
    altitude = [d for d in detections if d.rule == "altitude_divergence"]
    assert len(altitude) == 1
    assert "quality_note" not in altitude[0].evidence


def test_missing_position_altitude_divergence_fires_without_location():
    detections = run_track_detectors(
        [
            _point(
                1,
                1700000000,
                None,
                None,
                "simulated",
                baro_altitude_m=10000,
                geo_altitude_m=12000,
                quality_note="missing_coordinates",
            )
        ]
    )
    altitude = [d for d in detections if d.rule == "altitude_divergence"]
    assert len(altitude) == 1
    assert altitude[0].latitude is None
    assert altitude[0].longitude is None
    assert altitude[0].evidence["quality_note"] == "missing_coordinates"


def test_invalid_altitude_values_are_rejected_not_coerced():
    for baro, geo in (
        ("abc", 12000),
        (10000, "xyz"),
        (None, 12000),
        (10000, None),
    ):
        detections = run_track_detectors(
            [
                _point(
                    1,
                    1700000000,
                    19.0,
                    72.0,
                    "simulated",
                    baro_altitude_m=baro,
                    geo_altitude_m=geo,
                )
            ]
        )
        assert [d for d in detections if d.rule == "altitude_divergence"] == []


def test_normal_altitude_difference_does_not_fire():
    detections = run_track_detectors(
        [
            _point(
                1,
                1700000000,
                19.0,
                72.0,
                "simulated",
                baro_altitude_m=10000,
                geo_altitude_m=10100,
            )
        ]
    )
    assert [d for d in detections if d.rule == "altitude_divergence"] == []


def test_detect_api_reports_mixed_provenance_end_to_end(tmp_path):
    """The mixed label survives persistence, serialization, and the
    API response model — not just the in-memory detector."""
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.ingestion.pipeline import ingest_observations
    from app.main import create_app
    from app.store import get_connection, init_db

    db_path = tmp_path / "test.db"
    init_db(db_path)
    records = [
        {
            "icao24": "abcdef",
            "observed_at": 1700000000,
            "latitude": 19.0,
            "longitude": 72.0,
            "source": "opensky-live",
            "source_kind": "real",
        },
        {
            "icao24": "abcdef",
            "observed_at": 1700000010,
            "latitude": 20.3,
            "longitude": 72.0,
            "source": "spoofsky-demo",
            "source_kind": "simulated",
        },
    ]
    with get_connection(db_path) as connection:
        ingest_observations(connection, records)
    app = create_app(Settings(database_path=db_path))
    client = TestClient(app, headers={"X-Requested-With": "XMLHttpRequest"})
    response = client.post("/api/detect")
    assert response.status_code == 200
    teleport = [
        d for d in response.json()["detections"] if d["rule"] == "teleport"
    ]
    assert len(teleport) == 1
    assert teleport[0]["source_kind"] == "mixed"
    assert teleport[0]["source_kinds"] == ["real", "simulated"]
    # And the stored read-back agrees.
    stored = client.get("/api/anomalies?rule=teleport")
    assert stored.json()[0]["source_kind"] == "mixed"
