"""Versioned, leakage-safe trajectory features shared by optional models."""

from __future__ import annotations

from ..detectors.features import haversine_km, sort_track

FEATURE_SCHEMA_VERSION = "trajectory-v1"
FEATURE_NAMES = (
    "mean_groundspeed_ms",
    "max_speed_change_ms",
    "mean_vertical_rate_ms",
    "max_heading_change_deg",
    "mean_displacement_km",
    "max_gap_s",
    "missing_position_fraction",
)


def extract_track_features(observations: list[dict]) -> dict[str, float]:
    ordered = sort_track(observations)
    if not ordered:
        return {name: 0.0 for name in FEATURE_NAMES}
    speeds = [float(row["velocity_ms"]) for row in ordered if row.get("velocity_ms") is not None]
    vertical = [float(row["vertical_rate_ms"]) for row in ordered if row.get("vertical_rate_ms") is not None]
    headings = [float(row["heading_deg"]) for row in ordered if row.get("heading_deg") is not None]
    displacements = []
    gaps = []
    for previous, current in zip(ordered, ordered[1:]):
        dt = int(current["observed_at"]) - int(previous["observed_at"])
        gaps.append(max(0, dt))
        if all(previous.get(key) is not None and current.get(key) is not None for key in ("latitude", "longitude")):
            displacements.append(haversine_km(float(previous["latitude"]), float(previous["longitude"]), float(current["latitude"]), float(current["longitude"])))
    speed_changes = [abs(b - a) for a, b in zip(speeds, speeds[1:])]
    heading_changes = [abs(((b - a + 180) % 360) - 180) for a, b in zip(headings, headings[1:])]
    return {
        "mean_groundspeed_ms": sum(speeds) / len(speeds) if speeds else 0.0,
        "max_speed_change_ms": max(speed_changes, default=0.0),
        "mean_vertical_rate_ms": sum(vertical) / len(vertical) if vertical else 0.0,
        "max_heading_change_deg": max(heading_changes, default=0.0),
        "mean_displacement_km": sum(displacements) / len(displacements) if displacements else 0.0,
        "max_gap_s": float(max(gaps, default=0)),
        "missing_position_fraction": sum(1 for row in ordered if row.get("latitude") is None or row.get("longitude") is None) / len(ordered),
    }


def feature_vector(observations: list[dict]) -> list[float]:
    values = extract_track_features(observations)
    return [float(values[name]) for name in FEATURE_NAMES]
