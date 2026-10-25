"""Clearly labelled demonstration dataset.

Every record here has source='spoofsky-demo' and
source_kind='simulated'. It is test data for the pipeline and, later,
for the detectors. It must never be presented as real tracking data,
and it is never used as a substitute for a failed real source — the
live adapter returns an error instead (see opensky.py).

Contents (deterministic, base time 1700000000 = 2023-11-14T22:13:20Z):
- "abc123": a normal track, 6 points, steady north-east movement.
- "def456": a track with a large position jump between two consecutive
  points (detector fixture for a later stage; here it is just data).
- "123abc": a track whose geometric altitude diverges from its
  barometric altitude over time (later detector fixture).
- "111aaa", "222bbb", "333ccc": three aircraft whose tracks all shift
  by the same offset at the same step (later clustering fixture).
- One record with no coordinates (data-quality fixture).
- One out-of-order record (timestamp between two earlier points, but
  placed last in the list).
- One exact duplicate of the normal track's first point.

Total: 34 records. First load: 33 stored, 1 duplicate, 1
missing-coordinates, 0 stale. Second load: 0 stored, 34 duplicates.
"""

from __future__ import annotations

BASE_TIME = 1700000000
SOURCE = "spoofsky-demo"


def _point(
    icao24: str,
    offset: int,
    latitude: float | None,
    longitude: float | None,
    *,
    baro_altitude_m: float | None = 10000.0,
    geo_altitude_m: float | None = None,
    velocity_ms: float | None = 250.0,
    heading_deg: float | None = 45.0,
    callsign: str | None = None,
) -> dict:
    observed_at = BASE_TIME + offset
    return {
        "icao24": icao24,
        "observed_at": observed_at,
        "latitude": latitude,
        "longitude": longitude,
        "baro_altitude_m": baro_altitude_m,
        "geo_altitude_m": geo_altitude_m if geo_altitude_m is not None else baro_altitude_m,
        "velocity_ms": velocity_ms,
        "heading_deg": heading_deg,
        "callsign": callsign,
        "source": SOURCE,
        "source_kind": "simulated",
        "received_at": observed_at,
    }


def demo_observations() -> list[dict]:
    records: list[dict] = []

    # Normal track.
    for step in range(6):
        records.append(
            _point(
                "abc123",
                step * 10,
                19.07 + step * 0.02,
                72.87 + step * 0.03,
                baro_altitude_m=10000.0 + step * 10,
                callsign="NORM001",
            )
        )

    # Jump track: steps 0-1 near Mumbai, step 2 far to the north-east.
    records.append(_point("def456", 0, 19.0, 72.8, callsign="JUMP001"))
    records.append(_point("def456", 10, 19.01, 72.81, callsign="JUMP001"))
    records.append(_point("def456", 20, 25.0, 80.0, callsign="JUMP001"))
    records.append(_point("def456", 30, 25.01, 80.01, callsign="JUMP001"))
    records.append(_point("def456", 40, 25.02, 80.02, callsign="JUMP001"))

    # Altitude-divergence track: barometric steady, geometric climbing away.
    geo = [10000.0, 10200.0, 10500.0, 11000.0, 11800.0]
    for step in range(5):
        records.append(
            _point(
                "123abc",
                step * 10,
                19.5 + step * 0.01,
                73.0 + step * 0.01,
                baro_altitude_m=10000.0,
                geo_altitude_m=geo[step],
                callsign="ALT001",
            )
        )

    # Common-offset group: steady movement, then the same +0.4° shift
    # applied to all three aircraft at steps 3 and 4.
    bases = {"111aaa": (20.0, 74.0), "222bbb": (20.2, 74.2), "333ccc": (20.4, 74.4)}
    for icao24, (base_lat, base_lon) in bases.items():
        for step in range(5):
            offset = 0.4 if step >= 3 else 0.0
            records.append(
                _point(
                    icao24,
                    step * 10,
                    base_lat + step * 0.01 + offset,
                    base_lon + step * 0.01 + offset,
                    callsign="GRP001",
                )
            )

    # Data-quality fixture: no coordinates at all.
    records.append(
        {
            "icao24": "abc123",
            "observed_at": BASE_TIME + 1000,
            "latitude": None,
            "longitude": None,
            "baro_altitude_m": 10000.0,
            "source": SOURCE,
            "source_kind": "simulated",
            "received_at": BASE_TIME + 1000,
        }
    )

    # Out-of-order fixture: timestamp between normal steps 0 and 1,
    # deliberately placed at the end of the list. Its position lies
    # exactly on the normal track (the midpoint of steps 0 and 1), so
    # it tests ordering only and must not create a speed spike.
    records.append(
        _point("abc123", 5, 19.08, 72.885, baro_altitude_m=10005.0, callsign="NORM001")
    )

    # Duplicate fixture: exact copy of the normal track's first point.
    records.append(records[0])

    return records
