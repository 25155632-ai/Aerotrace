"""Pipeline outcomes: duplicates, stale, missing coords, order, provenance."""

from pathlib import Path

from app.ingestion.pipeline import ingest_observations
from app.store import get_connection, init_db, list_observations


def _record(icao, at, lat=19.0, lon=72.0, source="test", kind="simulated", received=None):
    record = {
        "icao24": icao,
        "observed_at": at,
        "latitude": lat,
        "longitude": lon,
        "source": source,
        "source_kind": kind,
    }
    if received is not None:
        record["received_at"] = received
    return record


def test_mixed_batch_report_and_order(tmp_path: Path) -> None:
    path = tmp_path / "test.db"
    init_db(path)
    batch = [
        _record("abc123", 100),
        _record("abc123", 110),
        _record("abc123", 100),  # exact duplicate
        _record("abc123", 100, lat=None, lon=None),  # same key, no position: distinct report, stored as missing
        _record("def456", 100, lat=None, lon=None),  # missing coordinates
        _record("123abc", 100, received=2000),  # stale: 1900s late
        _record("abc123", 105),  # out of order
        _record("bad999", 100, lat=999.0),  # rejected: latitude out of range
    ]
    with get_connection(path) as connection:
        report = ingest_observations(connection, batch)
        rows = list_observations(connection, icao24="abc123")
    # Note: the 4th record shares (icao24, observed_at, source) with the
    # first but reports no position, so under the position-aware
    # duplicate rule it is a distinct missing-coordinates record, not
    # a duplicate. Only the exact repeat (3rd record) is a duplicate.
    assert report.total == 8
    assert report.stored == 6
    assert report.duplicates == 1
    assert report.missing_coordinates == 2
    assert report.stale == 1
    assert len(report.rejected) == 1
    assert report.rejected[0].reason.startswith("latitude")
    # Out-of-order record slots into its track at read time.
    assert [row["observed_at"] for row in rows] == [100, 100, 105, 110]


def test_same_aircraft_time_different_source_is_not_duplicate(tmp_path: Path) -> None:
    path = tmp_path / "test.db"
    init_db(path)
    with get_connection(path) as connection:
        report = ingest_observations(
            connection,
            [
                _record("abc123", 100, source="source-a", kind="real"),
                _record("abc123", 100, source="source-b", kind="simulated"),
            ],
        )
        rows = list_observations(connection)
    assert report.duplicates == 0
    assert report.stored == 2
    assert {row["source_kind"] for row in rows} == {"real", "simulated"}


def test_quality_rows_are_flagged(tmp_path: Path) -> None:
    path = tmp_path / "test.db"
    init_db(path)
    with get_connection(path) as connection:
        ingest_observations(
            connection,
            [
                _record("def456", 100, lat=None, lon=None),
                _record("123abc", 100, received=2000),
            ],
        )
        rows = list_observations(connection)
    notes = {row["icao24"]: row["quality_note"] for row in rows}
    assert notes["def456"] == "missing_coordinates"
    assert notes["123abc"] == "stale"


def test_conflicting_position_same_timestamp_is_not_duplicate(tmp_path: Path) -> None:
    """Two different positions at the same aircraft/second/source are
    conflicting reports, not duplicates: both must survive ingestion
    so the timestamp_collision detector can flag them."""
    path = tmp_path / "test.db"
    init_db(path)
    with get_connection(path) as connection:
        report = ingest_observations(
            connection,
            [
                _record("abc123", 100, lat=19.0, lon=72.0),
                _record("abc123", 100, lat=20.0, lon=73.0),
            ],
        )
        rows = list_observations(connection, icao24="abc123")
    assert report.duplicates == 0
    assert report.stored == 2
    assert {(row["latitude"], row["longitude"]) for row in rows} == {
        (19.0, 72.0),
        (20.0, 73.0),
    }


def test_exact_repeat_after_conflict_is_still_duplicate(tmp_path: Path) -> None:
    path = tmp_path / "test.db"
    init_db(path)
    with get_connection(path) as connection:
        report = ingest_observations(
            connection,
            [
                _record("abc123", 100, lat=19.0, lon=72.0),
                _record("abc123", 100, lat=20.0, lon=73.0),
                _record("abc123", 100, lat=19.0, lon=72.0),
            ],
        )
    assert report.stored == 2
    assert report.duplicates == 1


def test_conflicting_pair_survives_to_collision_detection(tmp_path: Path) -> None:
    """End-to-end regression: the conflicting pair must reach the
    detectors, where it fires timestamp_collision citing both rows.
    Before the position-aware duplicate rule, the second record was
    dropped as a duplicate and no detection was possible."""
    from app.detectors.detectors import run_all_detectors

    path = tmp_path / "test.db"
    init_db(path)
    with get_connection(path) as connection:
        ingest_observations(
            connection,
            [
                _record("abc123", 100, lat=19.0, lon=72.0),
                _record("abc123", 100, lat=20.0, lon=73.0),
            ],
        )
        rows = [dict(row) for row in list_observations(connection)]
    detections = run_all_detectors(rows)
    assert [d.rule for d in detections] == ["timestamp_collision"]
    assert sorted(detections[0].supporting_observation_ids) == sorted(
        row["id"] for row in rows
    )
