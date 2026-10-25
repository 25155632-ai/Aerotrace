"""Migrations: a v1 database upgrades to v2 with its data intact."""

import sqlite3
from pathlib import Path

from app.store import (
    SCHEMA,
    applied_migrations,
    get_connection,
    init_db,
)


def test_v1_database_migrates_to_v2_preserving_rows(tmp_path: Path) -> None:
    path = tmp_path / "test.db"
    # Build a genuine v1 database: v1 schema, no migrations table.
    connection = sqlite3.connect(str(path))
    connection.executescript(SCHEMA)
    connection.execute(
        "INSERT INTO observations (icao24, observed_at, source, source_kind, "
        "received_at) VALUES (?, ?, ?, ?, ?)",
        ("abc123", 1700000000, "legacy", "simulated", 1700000000),
    )
    connection.commit()
    connection.close()

    init_db(path)
    with get_connection(path) as connection:
        assert applied_migrations(connection) == [1, 2, 3, 4, 5, 6, 7, 8, 9]
        index_names = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        assert {
            "idx_anomalies_detected_at",
            "idx_anomalies_rule_time",
            "idx_events_start_at",
        } <= index_names
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(observations)")
        }
        assert {"callsign", "quality_note"} <= columns
        anomaly_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(anomalies)")
        }
        assert {"kind", "latitude", "longitude"} <= anomaly_columns
        event_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(events)")
        }
        assert {"kind", "severity", "start_at", "aircraft_json"} <= event_columns
        row = connection.execute(
            "SELECT icao24, source_kind FROM observations"
        ).fetchone()
    assert row["icao24"] == "abc123"
    assert row["source_kind"] == "simulated"

    # Re-running is a no-op, not an error.
    init_db(path)
    with get_connection(path) as connection:
        assert applied_migrations(connection) == [1, 2, 3, 4, 5, 6, 7, 8, 9]


def test_v4_database_migrates_to_v5_backfilling_mixed_provenance(tmp_path):
    """A v4 database (pre-provenance-fix) upgrades to v5: the CHECK
    admits 'mixed', existing rows are backfilled from their supporting
    observations, and the v4 indexes survive the table rebuild."""
    import sqlite3 as _sqlite3

    from app.store import MIGRATIONS, get_connection, init_db

    path = tmp_path / "v4.db"
    connection = _sqlite3.connect(str(path))
    connection.row_factory = _sqlite3.Row
    connection.execute(
        "CREATE TABLE schema_migrations ("
        "version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL)"
    )
    for version, migration in MIGRATIONS:
        if version > 4:
            break
        migration(connection)
        connection.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (version, 1700000000),
        )
    # Two supporting observations of different kinds, one detection
    # that the old code labelled 'simulated'.
    connection.execute(
        "INSERT INTO observations (id, icao24, observed_at, source, source_kind, "
        "received_at) VALUES (1, 'abcdef', 1700000000, 'opensky-live', 'real', 1700000000)"
    )
    connection.execute(
        "INSERT INTO observations (id, icao24, observed_at, source, source_kind, "
        "received_at) VALUES (2, 'abcdef', 1700000010, 'spoofsky-demo', 'simulated', 1700000010)"
    )
    connection.execute(
        "INSERT INTO anomalies (id, icao24, detected_at, rule, severity, "
        "evidence_json, source_kind) VALUES (1, 'abcdef', 1700000010, 'teleport', "
        "'high', ?, 'simulated')",
        ('{"supporting_observation_ids": [1, 2]}',),
    )
    connection.commit()
    connection.close()

    init_db(path)
    with get_connection(path) as connection:
        row = connection.execute(
            "SELECT source_kind, source_kinds_json FROM anomalies WHERE id = 1"
        ).fetchone()
        assert row["source_kind"] == "mixed"
        assert row["source_kinds_json"] == '["real", "simulated"]'
        # The widened CHECK now accepts a fresh 'mixed' insert.
        connection.execute(
            "INSERT INTO anomalies (icao24, detected_at, rule, severity, "
            "evidence_json, source_kind, source_kinds_json) VALUES "
            "('abcdef', 1700000020, 'teleport', 'high', '{}', 'mixed', "
            "'[\"real\", \"simulated\"]')"
        )
        index_names = {
            r["name"]
            for r in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        assert {
            "idx_anomalies_aircraft_time",
            "idx_anomalies_detected_at",
            "idx_anomalies_rule_time",
        } <= index_names
