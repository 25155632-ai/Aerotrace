"""Feature extraction for the detection engine. No verdicts here.

This module turns stored observations into the quantities detectors
reason about: per-track segments (distance, elapsed time, implied
speed, course) and per-point altitude divergence. Detectors import
these features; they never recompute geometry themselves, so two
detectors can never disagree about what a segment's speed was.

Handling of awkward data (documented, not hidden):
- Points are sorted by (observed_at, id). Out-of-order arrival therefore
  never creates a spurious segment: the track is rebuilt in time order.
- Points with a non-NULL quality_note (stale or missing coordinates)
  are excluded from movement features. A stale position says nothing
  reliable about current movement, and a missing position has no
  geometry at all. Both are surfaced separately as data-quality
  findings by the detectors module.
- Points without coordinates are excluded from segments but kept for
  gap/missing analysis in the detectors module.
- A segment with dt == 0 is kept (not dropped) so the timestamp
  detector can flag two different positions reported at the same
  second; its speed is undefined (None), never zero.
- Irregular sampling is handled by construction: every quantity is
  per segment, using that segment's own elapsed time. No resampling,
  no interpolation, no assumed cadence — interpolating would invent
  positions the aircraft may never have reported.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

EARTH_RADIUS_KM = 6371.0


def haversine_km(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """Great-circle distance in kilometres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing from point 1 to point 2, in [0, 360)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    x = math.sin(dlambda) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def angular_diff_deg(a: float, b: float) -> float:
    """Smallest absolute difference between two headings, in [0, 180]."""
    return abs((a - b + 180.0) % 360.0 - 180.0)


@dataclass(frozen=True)
class Segment:
    start_id: int | None
    end_id: int | None
    start_at: int
    end_at: int
    dt_s: int
    distance_km: float
    speed_ms: float | None  # None when dt == 0
    bearing_deg: float
    start_lat: float
    start_lon: float
    end_lat: float
    end_lon: float


def sort_track(points: list[dict]) -> list[dict]:
    """Time-order a track. Records without an icao24 cannot belong to
    a track and are dropped here (the database enforces icao24 NOT
    NULL, so this only matters for direct function callers)."""
    usable = [p for p in points if p.get("icao24")]
    return sorted(usable, key=lambda p: (p["observed_at"], p.get("id") or 0))


def clean_points(points: list[dict]) -> list[dict]:
    """Points usable for movement analysis: positioned, and not
    flagged stale/missing by ingestion."""
    return [
        p
        for p in sort_track(points)
        if p.get("latitude") is not None
        and p.get("longitude") is not None
        and p.get("quality_note") is None
    ]


def segments_for_track(points: list[dict]) -> list[Segment]:
    clean = clean_points(points)
    segments: list[Segment] = []
    for start, end in zip(clean, clean[1:]):
        dt = int(end["observed_at"]) - int(start["observed_at"])
        distance = haversine_km(
            float(start["latitude"]),
            float(start["longitude"]),
            float(end["latitude"]),
            float(end["longitude"]),
        )
        segments.append(
            Segment(
                start_id=start.get("id"),
                end_id=end.get("id"),
                start_at=int(start["observed_at"]),
                end_at=int(end["observed_at"]),
                dt_s=dt,
                distance_km=distance,
                speed_ms=(distance * 1000.0 / dt) if dt > 0 else None,
                bearing_deg=bearing_deg(
                    float(start["latitude"]),
                    float(start["longitude"]),
                    float(end["latitude"]),
                    float(end["longitude"]),
                ),
                start_lat=float(start["latitude"]),
                start_lon=float(start["longitude"]),
                end_lat=float(end["latitude"]),
                end_lon=float(end["longitude"]),
            )
        )
    return segments
