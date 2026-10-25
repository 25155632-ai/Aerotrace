"""Ingestion pipeline: raw records in, a counted report out.

One batch never fails as a whole because one record is bad. Each record
gets exactly one outcome, reported in IngestReport:

- stored: valid, new, and clean.
- duplicates: same (icao24, observed_at, source, position) already
  stored. Skipped, not an error, not stored twice. A conflicting
  position at the same key is NOT a duplicate: it is stored, so the
  timestamp_collision detector can flag two different positions
  reported at the same second.
- stale: received_at - observed_at exceeds `stale_after_seconds`
  (default 300). Stored with quality_note='stale' for audit, but
  detectors must ignore it: a position reported minutes late says
  nothing reliable about where the aircraft is now. Live adapters pass
  wall-clock receipt time; replay/demo data passes received_at =
  observed_at, so it is never misflagged as stale.
- missing_coordinates: latitude or longitude absent. Stored with
  quality_note='missing_coordinates' and NULL coordinates, for audit.
  A record without a position cannot enter a track and cannot be an
  anomaly; storing it (rather than dropping it) keeps the data-quality
  signal visible.
- rejected: not stored at all, with a per-record reason (bad icao24,
  bad timestamp, out-of-range coordinate, ...). Rejections are data,
  not exceptions, at this layer.

Out-of-order records are stored as-is. Read time always sorts by
observed_at (see store.list_observations), so a late-arriving earlier
position simply slots into its track; it is not an error and not a
quality flag.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..store import insert_observation, observation_exists
from .observations import ObservationError, normalize_observation

DEFAULT_STALE_AFTER_SECONDS = 300


@dataclass
class Rejection:
    index: int
    reason: str


@dataclass
class IngestReport:
    total: int = 0
    stored: int = 0
    duplicates: int = 0
    stale: int = 0
    missing_coordinates: int = 0
    rejected: list[Rejection] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "stored": self.stored,
            "duplicates": self.duplicates,
            "stale": self.stale,
            "missing_coordinates": self.missing_coordinates,
            "rejected": [{"index": r.index, "reason": r.reason} for r in self.rejected],
        }


def ingest_observations(
    connection: sqlite3.Connection,
    raw_records: list[object],
    *,
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
) -> IngestReport:
    report = IngestReport(total=len(raw_records))
    for index, raw in enumerate(raw_records):
        try:
            obs = normalize_observation(raw)
        except ObservationError as exc:
            report.rejected.append(Rejection(index=index, reason=exc.reason))
            continue

        if observation_exists(
            connection,
            obs.icao24,
            obs.observed_at,
            obs.source,
            obs.latitude,
            obs.longitude,
        ):
            report.duplicates += 1
            continue

        quality_note: str | None = None
        if obs.latitude is None or obs.longitude is None:
            quality_note = "missing_coordinates"
            report.missing_coordinates += 1
        elif obs.received_at - obs.observed_at > stale_after_seconds:
            quality_note = "stale"
            report.stale += 1

        insert_observation(
            connection,
            icao24=obs.icao24,
            observed_at=obs.observed_at,
            latitude=obs.latitude,
            longitude=obs.longitude,
            baro_altitude_m=obs.baro_altitude_m,
            geo_altitude_m=obs.geo_altitude_m,
            velocity_ms=obs.velocity_ms,
            heading_deg=obs.heading_deg,
            callsign=obs.callsign,
            source=obs.source,
            source_kind=obs.source_kind,
            received_at=obs.received_at,
            quality_note=quality_note,
        )
        report.stored += 1
    return report
