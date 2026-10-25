"""SQLite storage for Aerotrace, with versioned migrations.

This module is the single place that touches the database, so a future
move to PostgreSQL/PostGIS rewrites this file, not the detectors or the
routes. All queries here are parameterised — never build SQL from user
input.

Migrations:
- v1 (shell stage): observations, anomalies, events, event_anomalies.
- v2 (ingestion stage): `observations.callsign` and
  `observations.quality_note`, plus a duplicate-lookup index.
  `quality_note` is NULL for clean observations and one of
  'missing_coordinates' / 'stale' otherwise. Detectors must ignore
  rows with a non-NULL quality_note; those rows exist for audit, not
  for analysis.
- v3 (detection stage): `anomalies.kind`, `anomalies.latitude`,
  `anomalies.longitude`; `events.kind`, `events.severity`,
- v5 (provenance fix): `anomalies.source_kind` admits 'mixed' and
  `anomalies.source_kinds_json` records the contributing kinds;
  existing rows are backfilled from their supporting observations.
  `events.start_at`, `events.end_at`, `events.centroid_lat`,
  `events.centroid_lon`, `events.aircraft_json`.
- v4 (security audit stage): indexes for investigation query
  patterns — `anomalies(detected_at, id)`,
  `anomalies(rule, detected_at, id)`, `events(start_at, id)`.

Schema notes:
- Every observation carries `source` and `source_kind` ('real' or
  'simulated'), enforced by a CHECK constraint, so provenance can never
  be silently lost.
- Anomalies store their evidence as JSON (rule, threshold, input
  values) so a result is always auditable back to its inputs.
- Events are 'candidate' by default. 'verified' requires a cited
  independent source recorded in `verification_source`; no code path
  may promote an event without one.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

# v1 schema, kept verbatim so a v1 database can be rebuilt in tests.
SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    icao24 TEXT NOT NULL,
    observed_at INTEGER NOT NULL,
    latitude REAL,
    longitude REAL,
    baro_altitude_m REAL,
    geo_altitude_m REAL,
    velocity_ms REAL,
    heading_deg REAL,
    source TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('real', 'simulated')),
    received_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_observations_aircraft_time
    ON observations (icao24, observed_at);
CREATE INDEX IF NOT EXISTS idx_observations_source_kind
    ON observations (source_kind);

CREATE TABLE IF NOT EXISTS anomalies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    icao24 TEXT NOT NULL,
    detected_at INTEGER NOT NULL,
    rule TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('low', 'medium', 'high')),
    evidence_json TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('real', 'simulated'))
);
CREATE INDEX IF NOT EXISTS idx_anomalies_aircraft_time
    ON anomalies (icao24, detected_at);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL DEFAULT 'candidate'
        CHECK (status IN ('candidate', 'verified')),
    summary TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    verification_source TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_status ON events (status);

CREATE TABLE IF NOT EXISTS event_anomalies (
    event_id INTEGER NOT NULL REFERENCES events (id),
    anomaly_id INTEGER NOT NULL REFERENCES anomalies (id),
    PRIMARY KEY (event_id, anomaly_id)
);
"""


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


def _migration_1(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)


def _migration_2(connection: sqlite3.Connection) -> None:
    existing = _columns(connection, "observations")
    if "callsign" not in existing:
        connection.execute("ALTER TABLE observations ADD COLUMN callsign TEXT")
    if "quality_note" not in existing:
        connection.execute("ALTER TABLE observations ADD COLUMN quality_note TEXT")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_observations_duplicate "
        "ON observations (icao24, observed_at, source)"
    )


def _migration_4(connection: sqlite3.Connection) -> None:
    # Indexes for the investigation query patterns (Stage 5): time
    # sorting, rule filtering, and event-window overlap. Added in the
    # Stage 6 audit after EXPLAIN QUERY PLAN showed full scans with
    # TEMP B-TREE sorts on all four patterns; at demo scale the scans
    # were sub-millisecond, so these exist to stop linear degradation
    # as tables grow, not to fix a measured slowdown.
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_anomalies_detected_at "
        "ON anomalies (detected_at, id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_anomalies_rule_time "
        "ON anomalies (rule, detected_at, id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_start_at "
        "ON events (start_at, id)"
    )


def _migration_3(connection: sqlite3.Connection) -> None:
    anomaly_columns = _columns(connection, "anomalies")
    for column, definition in (
        ("kind", "TEXT"),
        ("latitude", "REAL"),
        ("longitude", "REAL"),
    ):
        if column not in anomaly_columns:
            connection.execute(f"ALTER TABLE anomalies ADD COLUMN {column} {definition}")
    event_columns = _columns(connection, "events")
    for column, definition in (
        ("kind", "TEXT"),
        ("severity", "TEXT"),
        ("start_at", "INTEGER"),
        ("end_at", "INTEGER"),
        ("centroid_lat", "REAL"),
        ("centroid_lon", "REAL"),
        ("aircraft_json", "TEXT"),
    ):
        if column not in event_columns:
            connection.execute(f"ALTER TABLE events ADD COLUMN {column} {definition}")


def _migration_5(connection: sqlite3.Connection) -> None:
    """Provenance correction (audit fix): a detection's source_kind
    may be 'mixed', and its contributing kinds are stored explicitly.
    SQLite cannot widen a CHECK constraint in place, so the anomalies
    table is rebuilt. Existing rows are backfilled from the source
    kinds of their supporting observations (stored in evidence_json);
    rows without traceable supporting observations keep their stored
    label, so no history is rewritten on a guess."""
    if "source_kinds_json" in _columns(connection, "anomalies"):
        return
    # event_anomalies references anomalies by name, and SQLite rewrites
    # that reference to anomalies_v4 on RENAME (verified on this build:
    # PRAGMA legacy_alter_table does not prevent it). So the link table
    # is rebuilt alongside anomalies at the end of _migration_5_rebuild.
    _migration_5_rebuild(connection)


def _migration_5_rebuild(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        "SELECT id, source_kind, evidence_json FROM anomalies"
    ).fetchall()
    obs_kinds = {
        row["id"]: row["source_kind"]
        for row in connection.execute("SELECT id, source_kind FROM observations")
    }
    backfill: dict[int, tuple[str, str]] = {}
    for row in rows:
        try:
            supporting = json.loads(row["evidence_json"]).get(
                "supporting_observation_ids", []
            )
        except (ValueError, TypeError):
            supporting = []
        kinds = sorted({obs_kinds[i] for i in supporting if i in obs_kinds})
        if len(kinds) == 1:
            label = kinds[0]
        elif len(kinds) > 1:
            label = "mixed"
        else:
            label = row["source_kind"]
            kinds = [row["source_kind"]] if row["source_kind"] else []
        backfill[int(row["id"])] = (label, json.dumps(kinds))
    connection.execute("ALTER TABLE anomalies RENAME TO anomalies_v4")
    connection.execute(
        "CREATE TABLE anomalies ("
        "    id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "    icao24 TEXT NOT NULL,"
        "    detected_at INTEGER NOT NULL,"
        "    rule TEXT NOT NULL,"
        "    severity TEXT NOT NULL CHECK (severity IN ('low', 'medium', 'high')),"
        "    kind TEXT,"
        "    latitude REAL,"
        "    longitude REAL,"
        "    evidence_json TEXT NOT NULL,"
        "    source_kind TEXT NOT NULL"
        "        CHECK (source_kind IN ('real', 'simulated', 'mixed')),"
        "    source_kinds_json TEXT NOT NULL DEFAULT '[]'"
        ")"
    )
    connection.execute(
        "INSERT INTO anomalies (id, icao24, detected_at, rule, severity, "
        "kind, latitude, longitude, evidence_json, source_kind) "
        "SELECT id, icao24, detected_at, rule, severity, kind, latitude, "
        "longitude, evidence_json, source_kind FROM anomalies_v4"
    )
    for anomaly_id, (label, kinds_json) in backfill.items():
        connection.execute(
            "UPDATE anomalies SET source_kind = ?, source_kinds_json = ? "
            "WHERE id = ?",
            (label, kinds_json, anomaly_id),
        )
    connection.execute("DROP TABLE anomalies_v4")
    # Rebuild the link table: its anomaly FK was rewritten to
    # anomalies_v4 by the RENAME and would otherwise dangle.
    links = connection.execute(
        "SELECT event_id, anomaly_id FROM event_anomalies"
    ).fetchall()
    connection.execute("DROP TABLE event_anomalies")
    connection.execute(
        "CREATE TABLE event_anomalies ("
        "    event_id INTEGER NOT NULL REFERENCES events (id),"
        "    anomaly_id INTEGER NOT NULL REFERENCES anomalies (id),"
        "    PRIMARY KEY (event_id, anomaly_id)"
        ")"
    )
    for link in links:
        connection.execute(
            "INSERT OR IGNORE INTO event_anomalies (event_id, anomaly_id) "
            "VALUES (?, ?)",
            (link["event_id"], link["anomaly_id"]),
        )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_anomalies_aircraft_time "
        "ON anomalies (icao24, detected_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_anomalies_detected_at "
        "ON anomalies (detected_at, id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_anomalies_rule_time "
        "ON anomalies (rule, detected_at, id)"
    )


def _migration_6(connection: sqlite3.Connection) -> None:
    """Event verification records (remediation Batch B): one row per
    verification act. Rows are never deleted: superseding or revoking
    changes a row's status, so the history of what was claimed — and
    corrected — stays readable. The events.status column mirrors
    whether a 'current' verification row exists."""
    connection.execute(
        "CREATE TABLE IF NOT EXISTS event_verifications ("
        "    id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "    event_id INTEGER NOT NULL REFERENCES events (id),"
        "    title TEXT NOT NULL,"
        "    publisher TEXT,"
        "    reference_url TEXT,"
        "    accessed_date TEXT,"
        "    note TEXT NOT NULL,"
        "    submitted_by TEXT,"
        "    status TEXT NOT NULL DEFAULT 'current'"
        "        CHECK (status IN ('current', 'superseded', 'revoked')),"
        "    created_at INTEGER NOT NULL,"
        "    revoked_reason TEXT"
        ")"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_verifications_event "
        "ON event_verifications (event_id, created_at, id)"
    )


def _migration_7(connection: sqlite3.Connection) -> None:
    """Live-source fetch log (remediation Batch B): every OpenSky
    ingest attempt is recorded, so GET /api/sources reports the last
    real outcome instead of a hardcoded 'verified_live' claim. A fresh
    database has no rows and therefore reports 'not_verified'."""
    connection.execute(
        "CREATE TABLE IF NOT EXISTS source_fetch_log ("
        "    id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "    source TEXT NOT NULL,"
        "    outcome TEXT NOT NULL CHECK (outcome IN ('success', 'failed')),"
        "    fetched_at INTEGER NOT NULL,"
        "    records_stored INTEGER NOT NULL DEFAULT 0,"
        "    detail TEXT"
        ")"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_fetch_log_source "
        "ON source_fetch_log (source, fetched_at, id)"
    )


def _migration_8(connection: sqlite3.Connection) -> None:
    """Verification records survive re-detection. This fixes a real
    500 found by the replay-batch e2e tests: re-running detection
    after verifying an event failed with FOREIGN KEY constraint
    failed, because clear_detection_results deletes events that
    event_verifications rows reference.

    Two promises conflicted: verification rows are never deleted,
    and detection replaces all events. The resolution: event_id
    becomes nullable with ON DELETE SET NULL, so deleting events
    detaches their verification records instead of failing.
    Detached records keep their title, note, and citation in the
    database but no longer attach to any current event — a re-run's
    events start as candidates again, which is correct: new
    detections are new evidence, not automatically verified. The
    detection report counts detached records so the UI can say so.
    """
    connection.execute(
        "ALTER TABLE event_verifications RENAME TO event_verifications_v7"
    )
    connection.execute(
        "CREATE TABLE event_verifications ("
        "    id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "    event_id INTEGER REFERENCES events (id) ON DELETE SET NULL,"
        "    title TEXT NOT NULL,"
        "    publisher TEXT,"
        "    reference_url TEXT,"
        "    accessed_date TEXT,"
        "    note TEXT NOT NULL,"
        "    submitted_by TEXT,"
        "    status TEXT NOT NULL DEFAULT 'current'"
        "        CHECK (status IN ('current', 'superseded', 'revoked')),"
        "    created_at INTEGER NOT NULL,"
        "    revoked_reason TEXT"
        ")"
    )
    connection.execute(
        "INSERT INTO event_verifications (id, event_id, title, publisher, "
        "reference_url, accessed_date, note, submitted_by, status, "
        "created_at, revoked_reason) SELECT id, event_id, title, publisher, "
        "reference_url, accessed_date, note, submitted_by, status, "
        "created_at, revoked_reason FROM event_verifications_v7"
    )
    connection.execute("DROP TABLE event_verifications_v7")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_verifications_event "
        "ON event_verifications (event_id, created_at, id)"
    )


def _migration_9(connection: sqlite3.Connection) -> None:
    """Alerting, durable dispatch, delivery history, and model metadata.

    The alert tables deliberately do not foreign-key anomaly ids: detection
    runs replace derived rows, while alerts are an append-only audit of what
    was observed and notified. The JSON evidence retains the supporting ids
    and provenance even after a later detection run.
    """
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS alert_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
            min_severity TEXT NOT NULL DEFAULT 'high'
                CHECK (min_severity IN ('low', 'medium', 'high')),
            categories_json TEXT NOT NULL DEFAULT '[]',
            provenance_json TEXT NOT NULL DEFAULT '[]',
            region_json TEXT,
            min_isolation_anomaly_score REAL,
            supervised_threshold REAL,
            cooldown_seconds INTEGER NOT NULL DEFAULT 300
                CHECK (cooldown_seconds BETWEEN 0 AND 86400),
            group_window_seconds INTEGER NOT NULL DEFAULT 600
                CHECK (group_window_seconds BETWEEN 0 AND 86400),
            group_radius_km REAL NOT NULL DEFAULT 100
                CHECK (group_radius_km BETWEEN 0 AND 20000),
            channels_json TEXT NOT NULL DEFAULT '["in_app"]',
            in_app INTEGER NOT NULL DEFAULT 1 CHECK (in_app IN (0, 1)),
            external INTEGER NOT NULL DEFAULT 0 CHECK (external IN (0, 1)),
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_alert_rules_enabled ON alert_rules(enabled);

        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_id INTEGER NOT NULL REFERENCES alert_rules(id),
            fingerprint TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'open'
                CHECK (status IN ('open', 'acknowledged', 'resolved')),
            severity TEXT NOT NULL CHECK (severity IN ('low', 'medium', 'high')),
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            aircraft_json TEXT NOT NULL DEFAULT '[]',
            detected_at INTEGER NOT NULL,
            start_at INTEGER NOT NULL,
            end_at INTEGER NOT NULL,
            latitude REAL,
            longitude REAL,
            provenance TEXT NOT NULL
                CHECK (provenance IN ('real', 'simulated', 'mixed')),
            source_kinds_json TEXT NOT NULL DEFAULT '[]',
            supporting_event_ids_json TEXT NOT NULL DEFAULT '[]',
            supporting_detection_ids_json TEXT NOT NULL DEFAULT '[]',
            evidence_json TEXT NOT NULL DEFAULT '{}',
            created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at DESC, id DESC);
        CREATE INDEX IF NOT EXISTS idx_alerts_detected ON alerts(detected_at DESC, id DESC);

        CREATE TABLE IF NOT EXISTS alert_outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_id INTEGER NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'processing', 'sent', 'failed')),
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at INTEGER NOT NULL,
            last_error TEXT,
            created_at INTEGER NOT NULL,
            UNIQUE(alert_id, event_type)
        );
        CREATE INDEX IF NOT EXISTS idx_alert_outbox_ready
            ON alert_outbox(status, next_attempt_at, id);

        CREATE TABLE IF NOT EXISTS alert_delivery_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_id INTEGER NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
            channel TEXT NOT NULL,
            status TEXT NOT NULL
                CHECK (status IN ('sent', 'failed', 'disabled', 'skipped')),
            attempted_at INTEGER NOT NULL,
            provider_response TEXT,
            error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_alert_delivery_alert
            ON alert_delivery_attempts(alert_id, attempted_at DESC, id DESC);

        CREATE TABLE IF NOT EXISTS model_registry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            version TEXT,
            artifact_path TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            last_error TEXT,
            updated_at INTEGER NOT NULL
        );
        """
    )
    now = int(time.time())
    connection.execute(
        "INSERT OR IGNORE INTO alert_rules "
        "(name, enabled, min_severity, categories_json, provenance_json, "
        "cooldown_seconds, group_window_seconds, group_radius_km, "
        "channels_json, in_app, external, created_at, updated_at) "
        "VALUES ('default-high-severity', 1, 'high', '[]', '[]', 300, 600, 100, '[\"in_app\"]', 1, 0, ?, ?)",
        (now, now),
    )
    for name in ("isolation_forest", "xgboost", "lstm"):
        connection.execute(
            "INSERT OR IGNORE INTO model_registry "
            "(name, status, metadata_json, updated_at) VALUES (?, 'not_installed', '{}', ?)",
            (name, now),
        )


MIGRATIONS = [
    (1, _migration_1),
    (2, _migration_2),
    (3, _migration_3),
    (4, _migration_4),
    (5, _migration_5),
    (6, _migration_6),
    (7, _migration_7),
    (8, _migration_8),
    (9, _migration_9),
]


def log_source_fetch(
    connection: sqlite3.Connection,
    *,
    source: str,
    outcome: str,
    records_stored: int = 0,
    detail: str | None = None,
) -> None:
    """Record one live-fetch attempt. Detail is bounded and comes
    from the adapter's own generic errors, never from user input."""
    if "source_fetch_log" not in {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }:
        return
    connection.execute(
        "INSERT INTO source_fetch_log (source, outcome, fetched_at, "
        "records_stored, detail) VALUES (?, ?, ?, ?, ?)",
        (
            source,
            outcome,
            int(time.time()),
            records_stored,
            (detail or "")[:200] if detail else None,
        ),
    )


def latest_source_fetch(
    connection: sqlite3.Connection, source: str
) -> dict | None:
    """The most recent fetch attempt for a source, or None."""
    if "source_fetch_log" not in {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }:
        return None
    row = connection.execute(
        "SELECT source, outcome, fetched_at, records_stored, detail "
        "FROM source_fetch_log WHERE source = ? "
        "ORDER BY fetched_at DESC, id DESC LIMIT 1",
        (source,),
    ).fetchone()
    return dict(row) if row is not None else None


def migrate(connection: sqlite3.Connection) -> list[int]:
    """Apply pending migrations in order. Returns versions applied now."""
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL)"
    )
    applied = {
        row["version"]
        for row in connection.execute("SELECT version FROM schema_migrations")
    }
    newly_applied: list[int] = []
    for version, migration in MIGRATIONS:
        if version not in applied:
            migration(connection)
            connection.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, int(time.time())),
            )
            newly_applied.append(version)
    return newly_applied


def applied_migrations(connection: sqlite3.Connection) -> list[int]:
    rows = connection.execute(
        "SELECT version FROM schema_migrations ORDER BY version"
    ).fetchall()
    return [row["version"] for row in rows]


def get_connection(database_path: Path) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(database_path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db(database_path: Path) -> None:
    """Create or migrate the schema. Safe to call on startup and twice."""
    with get_connection(database_path) as connection:
        migrate(connection)


def table_names(database_path: Path) -> set[str]:
    with get_connection(database_path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    return {row["name"] for row in rows}


def evidence_to_json(evidence: dict) -> str:
    return json.dumps(evidence, sort_keys=True)


def observation_exists(
    connection: sqlite3.Connection,
    icao24: str,
    observed_at: int,
    source: str,
    latitude: float | None = None,
    longitude: float | None = None,
) -> bool:
    """Duplicate key: same aircraft, same second, same source, same
    reported position.

    The same aircraft/time from a *different* source is not a duplicate:
    it is a second, independent report and may matter for cross-checks.
    And the same aircraft/time/source at a *different* position is not
    a duplicate either: it is a conflicting report, and dropping it
    would destroy exactly the evidence the timestamp_collision
    detector exists to flag. Position comparison is NULL-safe, so two
    position-less reports at the same key still count as one duplicate.
    """
    row = connection.execute(
        "SELECT 1 FROM observations "
        "WHERE icao24 = ? AND observed_at = ? AND source = ? "
        "AND (latitude = ? OR (latitude IS NULL AND ? IS NULL)) "
        "AND (longitude = ? OR (longitude IS NULL AND ? IS NULL)) "
        "LIMIT 1",
        (icao24, observed_at, source, latitude, latitude, longitude, longitude),
    ).fetchone()
    return row is not None


def insert_observation(
    connection: sqlite3.Connection,
    *,
    icao24: str,
    observed_at: int,
    latitude: float | None,
    longitude: float | None,
    baro_altitude_m: float | None,
    geo_altitude_m: float | None,
    velocity_ms: float | None,
    heading_deg: float | None,
    callsign: str | None,
    source: str,
    source_kind: str,
    received_at: int,
    quality_note: str | None,
) -> int:
    cursor = connection.execute(
        "INSERT INTO observations (icao24, observed_at, latitude, longitude, "
        "baro_altitude_m, geo_altitude_m, velocity_ms, heading_deg, callsign, "
        "source, source_kind, received_at, quality_note) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            icao24,
            observed_at,
            latitude,
            longitude,
            baro_altitude_m,
            geo_altitude_m,
            velocity_ms,
            heading_deg,
            callsign,
            source,
            source_kind,
            received_at,
            quality_note,
        ),
    )
    return int(cursor.lastrowid)


def list_observations(
    connection: sqlite3.Connection,
    *,
    icao24: str | None = None,
    source_kind: str | None = None,
    from_ts: int | None = None,
    to_ts: int | None = None,
    ids: list[int] | None = None,
    limit: int = 100,
) -> list[dict]:
    """Stored observations, always ordered by time (then aircraft, id).

    Out-of-order ingestion is therefore invisible at read time: the
    caller always sees a time-ordered track, whatever order rows arrived.
    """
    # API reads cap at 1000 via their route; internal detection runs
    # may request up to 10000. Both caps are deliberate, not defaults
    # chosen by accident: unbounded reads would break the bounded-
    # memory rule for large datasets.
    limit = max(1, min(int(limit), 10000))
    query = (
        "SELECT id, icao24, observed_at, latitude, longitude, "
        "baro_altitude_m, geo_altitude_m, velocity_ms, heading_deg, "
        "callsign, source, source_kind, received_at, quality_note "
        "FROM observations"
    )
    conditions: list[str] = []
    params: list[object] = []
    if icao24 is not None:
        conditions.append("icao24 = ?")
        params.append(icao24)
    if source_kind is not None:
        conditions.append("source_kind = ?")
        params.append(source_kind)
    if from_ts is not None:
        conditions.append("observed_at >= ?")
        params.append(from_ts)
    if to_ts is not None:
        conditions.append("observed_at <= ?")
        params.append(to_ts)
    if ids is not None:
        if not ids:
            return []
        placeholders = ", ".join("?" for _ in ids)
        conditions.append(f"id IN ({placeholders})")
        params.extend(ids)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY observed_at ASC, icao24 ASC, id ASC LIMIT ?"
    params.append(limit)
    rows = connection.execute(query, params).fetchall()
    return [dict(row) for row in rows]


# --- Detection results -------------------------------------------------
# Detection runs replace previous results (see service.py): a run is a
# fresh analysis of current stored observations, not an append-only log.
# Clearing is deliberate and local-prototype-only behaviour, documented
# in docs/DETECTORS.md.


def count_attached_verifications(connection: sqlite3.Connection) -> int:
    """Verification records currently attached to an event. Used by
    detection runs to report how many records the run detaches (see
    migration 8): deleting events nulls their rows via ON DELETE
    SET NULL instead of failing. Returns 0 when the table is absent
    (a database that predates verification)."""
    table_exists = connection.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' "
        "AND name = 'event_verifications'"
    ).fetchone()[0] > 0
    if not table_exists:
        return 0
    return connection.execute(
        "SELECT COUNT(*) FROM event_verifications WHERE event_id IS NOT NULL"
    ).fetchone()[0]


def clear_detection_results(connection: sqlite3.Connection) -> None:
    connection.execute("DELETE FROM event_anomalies")
    connection.execute("DELETE FROM events")
    connection.execute("DELETE FROM anomalies")


def insert_anomaly(
    connection: sqlite3.Connection,
    *,
    icao24: str,
    detected_at: int,
    rule: str,
    severity: str,
    kind: str,
    latitude: float | None,
    longitude: float | None,
    evidence: dict,
    source_kind: str,
    supporting_observation_ids: list[int],
    source_kinds: list[str] | None = None,
) -> int:
    """Store one detection. Supporting observation ids are merged into
    the stored evidence JSON so a stored alert stays traceable even
    when read back without its run report. Contributing source kinds
    are stored alongside the label so a 'mixed' detection can explain
    itself; when omitted they default to the single label."""
    stored_evidence = {**evidence, "supporting_observation_ids": supporting_observation_ids}
    kinds = source_kinds if source_kinds is not None else [source_kind]
    if "source_kinds_json" in _columns(connection, "anomalies"):
        cursor = connection.execute(
            "INSERT INTO anomalies (icao24, detected_at, rule, severity, kind, "
            "latitude, longitude, evidence_json, source_kind, source_kinds_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                icao24,
                detected_at,
                rule,
                severity,
                kind,
                latitude,
                longitude,
                evidence_to_json(stored_evidence),
                source_kind,
                json.dumps(kinds),
            ),
        )
    else:  # Pre-v5 database: the kinds column does not exist yet.
        cursor = connection.execute(
            "INSERT INTO anomalies (icao24, detected_at, rule, severity, kind, "
            "latitude, longitude, evidence_json, source_kind) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                icao24,
                detected_at,
                rule,
                severity,
                kind,
                latitude,
                longitude,
                evidence_to_json(stored_evidence),
                source_kind,
            ),
        )
    return int(cursor.lastrowid)


def insert_event(
    connection: sqlite3.Connection,
    *,
    kind: str,
    severity: str,
    start_at: int,
    end_at: int,
    centroid_lat: float | None,
    centroid_lon: float | None,
    aircraft: list[str],
    summary: str,
    created_at: int,
) -> int:
    cursor = connection.execute(
        "INSERT INTO events (status, summary, created_at, kind, severity, "
        "start_at, end_at, centroid_lat, centroid_lon, aircraft_json) "
        "VALUES ('candidate', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            summary,
            created_at,
            kind,
            severity,
            start_at,
            end_at,
            centroid_lat,
            centroid_lon,
            json.dumps(aircraft),
        ),
    )
    return int(cursor.lastrowid)


def link_event_anomaly(
    connection: sqlite3.Connection, event_id: int, anomaly_id: int
) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO event_anomalies (event_id, anomaly_id) VALUES (?, ?)",
        (event_id, anomaly_id),
    )


ANOMALY_SORTS = {
    "detected_at_asc": "detected_at ASC, id ASC",
    "detected_at_desc": "detected_at DESC, id DESC",
    "severity_desc": (
        "CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, "
        "detected_at DESC, id DESC"
    ),
}


def list_anomalies(
    connection: sqlite3.Connection,
    *,
    rule: str | None = None,
    severity: str | None = None,
    kind: str | None = None,
    icao24: str | None = None,
    from_ts: int | None = None,
    to_ts: int | None = None,
    min_lat: float | None = None,
    max_lat: float | None = None,
    min_lon: float | None = None,
    max_lon: float | None = None,
    ids: list[int] | None = None,
    sort: str = "detected_at_desc",
    offset: int = 0,
    limit: int = 100,
) -> list[dict]:
    limit = max(1, min(int(limit), 10000))
    offset = max(0, int(offset))
    order_by = ANOMALY_SORTS.get(sort, ANOMALY_SORTS["detected_at_desc"])
    has_kinds = "source_kinds_json" in _columns(connection, "anomalies")
    query = (
        "SELECT id, icao24, detected_at, rule, severity, kind, latitude, "
        "longitude, evidence_json, source_kind"
        + (", source_kinds_json" if has_kinds else "")
        + " FROM anomalies"
    )
    conditions: list[str] = []
    params: list[object] = []
    if rule is not None:
        conditions.append("rule = ?")
        params.append(rule)
    if severity is not None:
        conditions.append("severity = ?")
        params.append(severity)
    if kind is not None:
        conditions.append("kind = ?")
        params.append(kind)
    if icao24 is not None:
        conditions.append("icao24 = ?")
        params.append(icao24)
    if from_ts is not None:
        conditions.append("detected_at >= ?")
        params.append(from_ts)
    if to_ts is not None:
        conditions.append("detected_at <= ?")
        params.append(to_ts)
    if min_lat is not None:
        conditions.append("latitude >= ?")
        params.append(min_lat)
    if max_lat is not None:
        conditions.append("latitude <= ?")
        params.append(max_lat)
    if min_lon is not None:
        conditions.append("longitude >= ?")
        params.append(min_lon)
    if max_lon is not None:
        conditions.append("longitude <= ?")
        params.append(max_lon)
    if ids is not None:
        if not ids:
            return []
        placeholders = ", ".join("?" for _ in ids)
        conditions.append(f"id IN ({placeholders})")
        params.extend(ids)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += f" ORDER BY {order_by} LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    results: list[dict] = []
    for row in connection.execute(query, params).fetchall():
        item = dict(row)
        evidence = json.loads(item.pop("evidence_json"))
        item["evidence"] = evidence
        item["supporting_observation_ids"] = evidence.get(
            "supporting_observation_ids", []
        )
        if has_kinds:
            try:
                item["source_kinds"] = json.loads(item.pop("source_kinds_json"))
            except (ValueError, TypeError):
                item["source_kinds"] = [item["source_kind"]]
        else:
            item["source_kinds"] = [item["source_kind"]]
        results.append(item)
    return results


def list_events(
    connection: sqlite3.Connection,
    *,
    kind: str | None = None,
    from_ts: int | None = None,
    to_ts: int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    limit = max(1, min(int(limit), 10000))
    offset = max(0, int(offset))
    query = (
        "SELECT id, status, summary, created_at, kind, severity, start_at, "
        "end_at, centroid_lat, centroid_lon, aircraft_json, "
        "verification_source FROM events"
    )
    conditions: list[str] = []
    params: list[object] = []
    if kind is not None:
        conditions.append("kind = ?")
        params.append(kind)
    # Window overlap: an event counts if its window intersects the
    # requested range, not only if it starts inside it.
    if from_ts is not None:
        conditions.append("end_at >= ?")
        params.append(from_ts)
    if to_ts is not None:
        conditions.append("start_at <= ?")
        params.append(to_ts)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY start_at ASC, id ASC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = connection.execute(query, params).fetchall()
    # One query for all member links, not one per event (N+1). Event
    # counts are small, so this was never a measured bottleneck — but
    # the single-query form is no harder to read, so there is no
    # reason to keep the per-event query.
    member_rows: list[sqlite3.Row] = []
    if rows:
        placeholders = ", ".join("?" for _ in rows)
        member_rows = connection.execute(
            "SELECT event_id, anomaly_id FROM event_anomalies "
            f"WHERE event_id IN ({placeholders}) ORDER BY anomaly_id ASC",
            [row["id"] for row in rows],
        ).fetchall()
    members_by_event: dict[int, list[int]] = {}
    for member_row in member_rows:
        members_by_event.setdefault(member_row["event_id"], []).append(
            member_row["anomaly_id"]
        )
    verifications_by_event: dict[int, list[dict]] = {}
    if rows and "event_verifications" in {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }:
        placeholders = ", ".join("?" for _ in rows)
        verification_rows = connection.execute(
            "SELECT id, event_id, title, publisher, reference_url, "
            "accessed_date, note, submitted_by, status, created_at, "
            "revoked_reason FROM event_verifications "
            f"WHERE event_id IN ({placeholders}) "
            "ORDER BY created_at DESC, id DESC",
            [row["id"] for row in rows],
        ).fetchall()
        for verification_row in verification_rows:
            verifications_by_event.setdefault(
                verification_row["event_id"], []
            ).append(dict(verification_row))
    results: list[dict] = []
    for row in rows:
        item = dict(row)
        item["aircraft"] = json.loads(item.pop("aircraft_json") or "[]")
        item["member_anomaly_ids"] = members_by_event.get(item["id"], [])
        history = verifications_by_event.get(item["id"], [])
        item["verification"] = next(
            (v for v in history if v["status"] == "current"), None
        )
        item["verification_history"] = history
        results.append(item)
    return results


def get_event(connection: sqlite3.Connection, event_id: int) -> dict | None:
    """One event with its members and verification records, or None."""
    row = connection.execute(
        "SELECT id, status, summary, created_at, kind, severity, start_at, "
        "end_at, centroid_lat, centroid_lon, aircraft_json, "
        "verification_source FROM events WHERE id = ?",
        (event_id,),
    ).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["aircraft"] = json.loads(item.pop("aircraft_json") or "[]")
    item["member_anomaly_ids"] = [
        r["anomaly_id"]
        for r in connection.execute(
            "SELECT anomaly_id FROM event_anomalies WHERE event_id = ? "
            "ORDER BY anomaly_id ASC",
            (event_id,),
        )
    ]
    has_verifications = connection.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' "
        "AND name = 'event_verifications'"
    ).fetchone()[0] > 0
    history = (
        [
            dict(r)
            for r in connection.execute(
                "SELECT id, event_id, title, publisher, reference_url, "
                "accessed_date, note, submitted_by, status, created_at, "
                "revoked_reason FROM event_verifications WHERE event_id = ? "
                "ORDER BY created_at DESC, id DESC",
                (event_id,),
            )
        ]
        if has_verifications
        else []
    )
    item["verification"] = next(
        (v for v in history if v["status"] == "current"), None
    )
    item["verification_history"] = history
    return item


def verify_event(
    connection: sqlite3.Connection,
    *,
    event_id: int,
    title: str,
    publisher: str | None,
    reference_url: str | None,
    accessed_date: str | None,
    note: str,
    submitted_by: str | None,
) -> dict:
    """Record a human-submitted citation and mark the event verified.
    If the event is already verified, the previous record is superseded
    (never deleted) and the new one becomes current. Raises LookupError
    for an unknown event. The caller commits; on exception the
    transaction rolls back, so status and record cannot half-update."""
    row = connection.execute(
        "SELECT status FROM events WHERE id = ?", (event_id,)
    ).fetchone()
    if row is None:
        raise LookupError(f"unknown event {event_id}")
    connection.execute(
        "UPDATE event_verifications SET status = 'superseded' "
        "WHERE event_id = ? AND status = 'current'",
        (event_id,),
    )
    cursor = connection.execute(
        "INSERT INTO event_verifications (event_id, title, publisher, "
        "reference_url, accessed_date, note, submitted_by, status, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'current', ?)",
        (
            event_id,
            title,
            publisher,
            reference_url,
            accessed_date,
            note,
            submitted_by,
            int(time.time()),
        ),
    )
    connection.execute(
        "UPDATE events SET status = 'verified' WHERE id = ?", (event_id,)
    )
    created = connection.execute(
        "SELECT id, event_id, title, publisher, reference_url, "
        "accessed_date, note, submitted_by, status, created_at, "
        "revoked_reason FROM event_verifications WHERE id = ?",
        (cursor.lastrowid,),
    ).fetchone()
    return dict(created)


def revoke_event(
    connection: sqlite3.Connection,
    *,
    event_id: int,
    reason: str | None,
) -> dict:
    """Revoke the current verification, returning the event to
    candidate. The revoked record is kept with its reason. Raises
    LookupError for an unknown event and ValueError when the event is
    not currently verified."""
    row = connection.execute(
        "SELECT status FROM events WHERE id = ?", (event_id,)
    ).fetchone()
    if row is None:
        raise LookupError(f"unknown event {event_id}")
    if row["status"] != "verified":
        raise ValueError(f"event {event_id} is not verified")
    connection.execute(
        "UPDATE event_verifications SET status = 'revoked', "
        "revoked_reason = ? WHERE event_id = ? AND status = 'current'",
        (reason, event_id),
    )
    connection.execute(
        "UPDATE events SET status = 'candidate' WHERE id = ?", (event_id,)
    )
    revoked = connection.execute(
        "SELECT id, event_id, title, publisher, reference_url, "
        "accessed_date, note, submitted_by, status, created_at, "
        "revoked_reason FROM event_verifications WHERE event_id = ? "
        "AND status = 'revoked' ORDER BY created_at DESC, id DESC LIMIT 1",
        (event_id,),
    ).fetchone()
    return dict(revoked)


# --- Alerting -----------------------------------------------------------


def _json_list(value: str | None) -> list:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _json_object(value: str | None) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def list_alert_rules(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute(
        "SELECT * FROM alert_rules ORDER BY id ASC"
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        for key in ("categories_json", "provenance_json", "channels_json"):
            item[key.removesuffix("_json")] = _json_list(item.pop(key))
        item["region"] = _json_object(item.pop("region_json"))
        item["enabled"] = bool(item["enabled"])
        item["in_app"] = bool(item["in_app"])
        item["external"] = bool(item["external"])
        result.append(item)
    return result


def get_alert_rule(connection: sqlite3.Connection, rule_id: int) -> dict | None:
    rows = list_alert_rules(connection)
    return next((row for row in rows if row["id"] == rule_id), None)


def upsert_alert_rule(connection: sqlite3.Connection, payload: dict, rule_id: int | None = None) -> dict:
    now = int(time.time())
    args = (
        payload["name"], int(payload["enabled"]), payload["min_severity"],
        json.dumps(payload.get("categories", [])),
        json.dumps(payload.get("provenance", [])),
        json.dumps(payload.get("region") or {}) if payload.get("region") else None,
        payload.get("min_isolation_anomaly_score"), payload.get("supervised_threshold"),
        payload["cooldown_seconds"], payload["group_window_seconds"],
        payload["group_radius_km"], json.dumps(payload.get("channels", ["in_app"])),
        int(payload.get("in_app", True)), int(payload.get("external", False)),
    )
    if rule_id is None:
        cursor = connection.execute(
            "INSERT INTO alert_rules (name, enabled, min_severity, categories_json, "
            "provenance_json, region_json, min_isolation_anomaly_score, supervised_threshold, "
            "cooldown_seconds, group_window_seconds, group_radius_km, channels_json, in_app, "
            "external, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (*args, now, now),
        )
        rule_id = int(cursor.lastrowid)
    else:
        connection.execute(
            "UPDATE alert_rules SET name=?, enabled=?, min_severity=?, categories_json=?, "
            "provenance_json=?, region_json=?, min_isolation_anomaly_score=?, supervised_threshold=?, "
            "cooldown_seconds=?, group_window_seconds=?, group_radius_km=?, channels_json=?, in_app=?, "
            "external=?, updated_at=? WHERE id=?",
            (*args, now, rule_id),
        )
    connection.commit()
    result = get_alert_rule(connection, rule_id)
    if result is None:
        raise LookupError(f"unknown alert rule {rule_id}")
    return result


def list_alerts(
    connection: sqlite3.Connection,
    *,
    severity: str | None = None,
    category: str | None = None,
    provenance: str | None = None,
    status: str | None = None,
    from_ts: int | None = None,
    to_ts: int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    query = "SELECT * FROM alerts"
    conditions: list[str] = []
    params: list[object] = []
    for column, value in (("severity", severity), ("category", category), ("provenance", provenance), ("status", status)):
        if value is not None:
            conditions.append(f"{column} = ?")
            params.append(value)
    if from_ts is not None:
        conditions.append("detected_at >= ?")
        params.append(from_ts)
    if to_ts is not None:
        conditions.append("detected_at <= ?")
        params.append(to_ts)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY detected_at DESC, id DESC LIMIT ? OFFSET ?"
    params.extend([max(1, min(int(limit), 1000)), max(0, int(offset))])
    result = []
    for row in connection.execute(query, params).fetchall():
        item = dict(row)
        item["aircraft"] = _json_list(item.pop("aircraft_json"))
        item["source_kinds"] = _json_list(item.pop("source_kinds_json"))
        item["supporting_event_ids"] = _json_list(item.pop("supporting_event_ids_json"))
        item["supporting_detection_ids"] = _json_list(item.pop("supporting_detection_ids_json"))
        item["evidence"] = _json_object(item.pop("evidence_json"))
        result.append(item)
    return result


def get_alert(connection: sqlite3.Connection, alert_id: int) -> dict | None:
    row = connection.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["aircraft"] = _json_list(item.pop("aircraft_json"))
    item["source_kinds"] = _json_list(item.pop("source_kinds_json"))
    item["supporting_event_ids"] = _json_list(item.pop("supporting_event_ids_json"))
    item["supporting_detection_ids"] = _json_list(item.pop("supporting_detection_ids_json"))
    item["evidence"] = _json_object(item.pop("evidence_json"))
    return item


def insert_alert(
    connection: sqlite3.Connection,
    *,
    rule_id: int,
    fingerprint: str,
    severity: str,
    category: str,
    title: str,
    summary: str,
    aircraft: list[str],
    detected_at: int,
    start_at: int,
    end_at: int,
    latitude: float | None,
    longitude: float | None,
    provenance: str,
    source_kinds: list[str],
    supporting_event_ids: list[int],
    supporting_detection_ids: list[int],
    evidence: dict,
) -> int | None:
    try:
        cursor = connection.execute(
            "INSERT INTO alerts (rule_id, fingerprint, severity, category, title, summary, "
            "aircraft_json, detected_at, start_at, end_at, latitude, longitude, provenance, "
            "source_kinds_json, supporting_event_ids_json, supporting_detection_ids_json, "
            "evidence_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                rule_id, fingerprint, severity, category, title, summary,
                json.dumps(aircraft), detected_at, start_at, end_at, latitude, longitude,
                provenance, json.dumps(source_kinds), json.dumps(supporting_event_ids),
                json.dumps(supporting_detection_ids), json.dumps(evidence, sort_keys=True), int(time.time()),
            ),
        )
    except sqlite3.IntegrityError:
        return None
    return int(cursor.lastrowid)


def enqueue_alert_outbox(connection: sqlite3.Connection, alert_id: int, payload: dict) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO alert_outbox (alert_id, event_type, payload_json, next_attempt_at, created_at) "
        "VALUES (?, 'alert.created', ?, ?, ?)",
        (alert_id, json.dumps(payload, sort_keys=True), int(time.time()), int(time.time())),
    )


def claim_outbox(connection: sqlite3.Connection, *, now: int, limit: int = 20) -> list[dict]:
    rows = connection.execute(
        "SELECT id, alert_id, event_type, payload_json, attempts FROM alert_outbox "
        "WHERE status IN ('pending', 'processing') AND next_attempt_at <= ? "
        "ORDER BY id ASC LIMIT ?", (now, max(1, min(limit, 100)),)
    ).fetchall()
    claimed = []
    for row in rows:
        connection.execute(
            "UPDATE alert_outbox SET status='processing', attempts=attempts+1 WHERE id=?",
            (row["id"],),
        )
        item = dict(row)
        item["payload"] = _json_object(item.pop("payload_json"))
        item["attempts"] = int(item["attempts"]) + 1
        claimed.append(item)
    connection.commit()
    return claimed


def finish_outbox(connection: sqlite3.Connection, outbox_id: int, *, success: bool, error: str | None = None, retry_at: int | None = None) -> None:
    if success:
        connection.execute("UPDATE alert_outbox SET status='sent', last_error=NULL WHERE id=?", (outbox_id,))
    elif retry_at is None:
        connection.execute("UPDATE alert_outbox SET status='failed', last_error=? WHERE id=?", ((error or "delivery failed")[:500], outbox_id))
    else:
        connection.execute("UPDATE alert_outbox SET status='pending', next_attempt_at=?, last_error=? WHERE id=?", (retry_at, (error or "delivery failed")[:500], outbox_id))
    connection.commit()


def record_delivery_attempt(connection: sqlite3.Connection, *, alert_id: int, channel: str, status: str, provider_response: str | None = None, error: str | None = None) -> None:
    connection.execute(
        "INSERT INTO alert_delivery_attempts (alert_id, channel, status, attempted_at, provider_response, error) VALUES (?, ?, ?, ?, ?, ?)",
        (alert_id, channel, status, int(time.time()), (provider_response or "")[:500] or None, (error or "")[:500] or None),
    )
    connection.commit()


def list_delivery_attempts(connection: sqlite3.Connection, alert_id: int) -> list[dict]:
    return [dict(row) for row in connection.execute(
        "SELECT id, alert_id, channel, status, attempted_at, provider_response, error "
        "FROM alert_delivery_attempts WHERE alert_id=? ORDER BY attempted_at DESC, id DESC", (alert_id,)
    ).fetchall()]


def list_model_registry(connection: sqlite3.Connection) -> list[dict]:
    result = []
    for row in connection.execute("SELECT * FROM model_registry ORDER BY name").fetchall():
        item = dict(row)
        item["metadata"] = _json_object(item.pop("metadata_json"))
        result.append(item)
    return result


def update_model_registry(connection: sqlite3.Connection, name: str, *, status: str, version: str | None = None, artifact_path: str | None = None, metadata: dict | None = None, last_error: str | None = None) -> None:
    connection.execute(
        "UPDATE model_registry SET status=?, version=?, artifact_path=?, metadata_json=?, last_error=?, updated_at=? WHERE name=?",
        (status, version, artifact_path, json.dumps(metadata or {}, sort_keys=True), (last_error or "")[:500] or None, int(time.time()), name),
    )
    connection.commit()
