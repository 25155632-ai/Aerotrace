"""SQLite schema: tables, provenance constraint, parameterised writes."""

import sqlite3
from pathlib import Path

import pytest

from app.store import get_connection, init_db, table_names


def test_init_db_creates_expected_tables(tmp_path: Path) -> None:
    path = tmp_path / "spoofsky.db"
    init_db(path)
    assert {"observations", "anomalies", "events", "event_anomalies"} <= table_names(path)


def test_source_kind_constraint_rejects_unknown_values(tmp_path: Path) -> None:
    path = tmp_path / "spoofsky.db"
    init_db(path)
    with get_connection(path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO observations (icao24, observed_at, source, "
                "source_kind, received_at) VALUES (?, ?, ?, ?, ?)",
                ("abc123", 1700000000, "simulated-fixture", "bogus", 1700000001),
            )


def test_parameterised_insert_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "spoofsky.db"
    init_db(path)
    with get_connection(path) as connection:
        connection.execute(
            "INSERT INTO observations (icao24, observed_at, latitude, "
            "longitude, source, source_kind, received_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("abc123", 1700000000, 19.07, 72.87, "simulated-fixture", "simulated", 1700000001),
        )
        row = connection.execute(
            "SELECT icao24, source_kind FROM observations WHERE icao24 = ?",
            ("abc123",),
        ).fetchone()
    assert row["icao24"] == "abc123"
    assert row["source_kind"] == "simulated"


def test_observation_exists_is_position_aware(tmp_path: Path) -> None:
    from app.store import insert_observation, observation_exists

    path = tmp_path / "spoofsky.db"
    init_db(path)
    with get_connection(path) as connection:
        insert_observation(
            connection,
            icao24="abc123",
            observed_at=100,
            latitude=19.0,
            longitude=72.0,
            baro_altitude_m=None,
            geo_altitude_m=None,
            velocity_ms=None,
            heading_deg=None,
            callsign=None,
            source="test",
            source_kind="simulated",
            received_at=100,
            quality_note=None,
        )
        assert observation_exists(connection, "abc123", 100, "test", 19.0, 72.0)
        assert not observation_exists(connection, "abc123", 100, "test", 20.0, 73.0)
        assert not observation_exists(connection, "abc123", 100, "other", 19.0, 72.0)
        # NULL-safe: a position-less report is not the same as a
        # positioned one, but two position-less reports are duplicates.
        assert not observation_exists(connection, "abc123", 100, "test", None, None)
        insert_observation(
            connection,
            icao24="abc123",
            observed_at=100,
            latitude=None,
            longitude=None,
            baro_altitude_m=None,
            geo_altitude_m=None,
            velocity_ms=None,
            heading_deg=None,
            callsign=None,
            source="test",
            source_kind="simulated",
            received_at=100,
            quality_note="missing_coordinates",
        )
        assert observation_exists(connection, "abc123", 100, "test", None, None)
