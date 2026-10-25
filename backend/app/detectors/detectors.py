"""Anomaly detectors. Pure functions: tracks in, detections out.

Every detector returns Detection objects, never a bare score and never
a verdict about spoofing. A Detection carries its rule, severity, the
observations that support it (by stored id), and the evidence that
produced it (inputs, thresholds, computed values), so any alert can be
traced back to the exact observations behind it.

Language discipline (project rule): detectors emit "unusual movement",
"navigation anomaly", and "potential interference indicator" — never
"GPS spoofing confirmed". A single detector firing is an anomaly, not
a cause.

Thresholds (DetectorConfig, all documented in docs/DETECTORS.md):
- max_speed_ms = 500: above the fastest civil cruise (~280 m/s) with
  a large margin for wind and noise, far below the demo jumps
  (thousands of m/s). A faster threshold would miss obvious jumps;
  a lower one would flag ordinary fast jets.
- max_acceleration_ms2 = 10: sustained civil acceleration is a few
  m/s^2; 10 leaves room for noisy segments without excusing a
  100-to-300 m/s jump in 10 s (20 m/s^2).
- max_heading_change_rate_deg_s = 20: a standard-rate civil turn is
  3 deg/s; 20 excuses steep but ordinary manoeuvres and still flags
  an instant reversal.
- course_change_deg = 120 with a 120 s window: a computed course
  reversal, used when reported headings are absent.
- altitude divergence: >500 m medium, >1500 m high. Below 500 m the
  two altitude sources routinely disagree (pressure vs geometry).
- track gap: >600 s between consecutive stored observations is a
  data-quality finding, not an anomaly — receiver coverage gaps are
  ordinary.

Failure modes are per detector, in docs/DETECTORS.md. The common ones:
long time gaps dilute average speed (a jump hidden inside a long gap
may not exceed the speed threshold); noisy single points can spike a
segment speed (mitigated by the generous thresholds, not eliminated);
reported headings may be absent (heading detector then abstains, it
does not guess from course — the course detector covers that case
separately).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .features import (
    angular_diff_deg,
    bearing_deg,
    clean_points,
    haversine_km,
    segments_for_track,
    sort_track,
)


@dataclass(frozen=True)
class DetectorConfig:
    max_speed_ms: float = 500.0
    max_acceleration_ms2: float = 10.0
    max_heading_change_rate_deg_s: float = 20.0
    course_change_deg: float = 120.0
    course_change_window_s: int = 120
    altitude_divergence_medium_m: float = 500.0
    altitude_divergence_high_m: float = 1500.0
    track_gap_s: int = 600
    # Common-mode offsets: jump-like segments (speed above the
    # teleport threshold) whose displacement vectors agree across
    # aircraft within a short window. A 50 km floor keeps ordinary
    # fast flight out; agreement is direction within 15 degrees and
    # magnitude within 25 percent.
    common_mode_min_offset_km: float = 50.0
    common_mode_window_s: int = 300
    common_mode_direction_tol_deg: float = 15.0
    common_mode_magnitude_tol: float = 0.25
    # Circle-spoof pattern: at least 3 aircraft curving around a
    # shared fitted centre. Centres are planar fits (see below),
    # so the 50 km tolerance is generous on purpose; this is a
    # candidate pattern flag, never a proof.
    circle_min_aircraft: int = 3
    circle_center_tol_km: float = 50.0
    circle_radius_max_km: float = 1000.0
    circle_min_turn_deg: float = 15.0
    circle_window_s: int = 600


@dataclass
class Detection:
    rule: str
    severity: str  # 'low' | 'medium' | 'high'
    kind: str  # 'anomaly' | 'data_quality'
    icao24: str
    detected_at: int
    latitude: float | None
    longitude: float | None
    evidence: dict
    supporting_observation_ids: list[int] = field(default_factory=list)
    source_kind: str = "simulated"
    # Distinct source kinds among the detection's supporting
    # observations (e.g. ["real", "simulated"] when mixed).
    source_kinds: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "kind": self.kind,
            "icao24": self.icao24,
            "detected_at": self.detected_at,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "evidence": self.evidence,
            "supporting_observation_ids": self.supporting_observation_ids,
            "source_kind": self.source_kind,
            "source_kinds": self.source_kinds,
        }


def _source_kinds(*points: dict) -> list[str]:
    """Distinct source kinds among the given points, sorted. Points
    without a source_kind contribute nothing."""
    return sorted({p["source_kind"] for p in points if p.get("source_kind")})


def _provenance(*points: dict) -> tuple[str, list[str]]:
    """Provenance label for a detection from its supporting
    observations: the single kind when homogeneous, "mixed" when real
    and simulated observations combine. With no provenance supplied,
    the legacy "simulated" fallback is kept (never "real"): a
    detection must not claim real provenance it cannot show."""
    kinds = _source_kinds(*points)
    if len(kinds) == 1:
        return kinds[0], kinds
    if len(kinds) > 1:
        return "mixed", kinds
    return "simulated", kinds


def _source_kind(points: list[dict]) -> str:
    """Legacy whole-track label, kept for callers that pass a track.
    Detectors use _provenance on supporting observations instead."""
    return _provenance(*points)[0]


def _ids(*points: dict) -> list[int]:
    return [int(p["id"]) for p in points if p.get("id") is not None]


def detect_teleport(
    points: list[dict], config: DetectorConfig | None = None
) -> list[Detection]:
    """Implausible displacement: segment implied speed above threshold,
    or two different positions at the same timestamp."""
    config = config or DetectorConfig()
    icao24 = points[0]["icao24"] if points else ""
    detections: list[Detection] = []
    for segment in segments_for_track(points):
        seg_points = _points_from_segment(points, segment)
        label, kinds = _provenance(*seg_points)
        if segment.dt_s == 0:
            if segment.distance_km > 0.1:
                detections.append(
                    Detection(
                        rule="timestamp_collision",
                        severity="high",
                        kind="anomaly",
                        icao24=icao24,
                        detected_at=segment.end_at,
                        latitude=segment.end_lat,
                        longitude=segment.end_lon,
                        evidence={
                            "rule": "timestamp_collision",
                            "distance_km": round(segment.distance_km, 3),
                            "dt_s": 0,
                            "description": "Two different positions reported at the same timestamp.",
                        },
                        supporting_observation_ids=_ids_from_segment(points, segment),
                        source_kind=label,
                        source_kinds=kinds,
                    )
                )
            continue
        if segment.speed_ms is not None and segment.speed_ms > config.max_speed_ms:
            detections.append(
                Detection(
                    rule="teleport",
                    severity="high" if segment.speed_ms > 1000.0 else "medium",
                    kind="anomaly",
                    icao24=icao24,
                    detected_at=segment.end_at,
                    latitude=segment.end_lat,
                    longitude=segment.end_lon,
                    evidence={
                        "rule": "teleport",
                        "distance_km": round(segment.distance_km, 3),
                        "dt_s": segment.dt_s,
                        "implied_speed_ms": round(segment.speed_ms, 1),
                        "threshold_speed_ms": config.max_speed_ms,
                        "description": "Unusual movement: implied speed between consecutive positions exceeds the plausibility threshold.",
                    },
                    supporting_observation_ids=_ids_from_segment(points, segment),
                    source_kind=label,
                    source_kinds=kinds,
                )
            )
    return detections


def _points_from_segment(points: list[dict], segment) -> list[dict]:
    by_id = {p.get("id"): p for p in points}
    return [p for p in (by_id.get(segment.start_id), by_id.get(segment.end_id)) if p is not None]


def _ids_from_segment(points: list[dict], segment) -> list[int]:
    return [int(p["id"]) for p in _points_from_segment(points, segment) if p.get("id") is not None]


def detect_acceleration(
    points: list[dict], config: DetectorConfig | None = None
) -> list[Detection]:
    """Physically implausible speed change between consecutive segments."""
    config = config or DetectorConfig()
    icao24 = points[0]["icao24"] if points else ""
    segments = segments_for_track(points)
    detections: list[Detection] = []
    for first, second in zip(segments, segments[1:]):
        if first.speed_ms is None or second.speed_ms is None:
            continue
        mid_gap = (second.start_at + second.end_at) / 2 - (first.start_at + first.end_at) / 2
        if mid_gap <= 0:
            continue
        acceleration = abs(second.speed_ms - first.speed_ms) / mid_gap
        if acceleration > config.max_acceleration_ms2:
            detections.append(
                Detection(
                    rule="implausible_acceleration",
                    severity="high" if acceleration > 25.0 else "medium",
                    kind="anomaly",
                    icao24=icao24,
                    detected_at=second.end_at,
                    latitude=second.end_lat,
                    longitude=second.end_lon,
                    evidence={
                        "rule": "implausible_acceleration",
                        "speed_before_ms": round(first.speed_ms, 1),
                        "speed_after_ms": round(second.speed_ms, 1),
                        "acceleration_ms2": round(acceleration, 2),
                        "threshold_acceleration_ms2": config.max_acceleration_ms2,
                        "description": "Unusual movement: speed changed faster than the plausibility threshold between consecutive segments.",
                    },
                    supporting_observation_ids=_ids_from_segment(points, second),
                    source_kind=_provenance(*_points_from_segment(points, second))[0],
                    source_kinds=_provenance(*_points_from_segment(points, second))[1],
                )
            )
    return detections


def detect_heading_change(
    points: list[dict], config: DetectorConfig | None = None
) -> list[Detection]:
    """Sharp reported-heading change: an instant reversal at speed.
    Abstains when headings are absent (the course detector covers that
    case from positions alone)."""
    config = config or DetectorConfig()
    clean = clean_points(points)
    icao24 = points[0]["icao24"] if points else ""
    detections: list[Detection] = []
    for prev, cur in zip(clean, clean[1:]):
        if prev.get("heading_deg") is None or cur.get("heading_deg") is None:
            continue
        dt = int(cur["observed_at"]) - int(prev["observed_at"])
        if dt <= 0:
            continue
        change = angular_diff_deg(float(prev["heading_deg"]), float(cur["heading_deg"]))
        rate = change / dt
        speed = cur.get("velocity_ms")
        if rate > config.max_heading_change_rate_deg_s and (
            speed is None or float(speed) > 50.0
        ):
            detections.append(
                Detection(
                    rule="sharp_heading_change",
                    severity="medium",
                    kind="anomaly",
                    icao24=icao24,
                    detected_at=int(cur["observed_at"]),
                    latitude=float(cur["latitude"]),
                    longitude=float(cur["longitude"]),
                    evidence={
                        "rule": "sharp_heading_change",
                        "heading_before_deg": float(prev["heading_deg"]),
                        "heading_after_deg": float(cur["heading_deg"]),
                        "change_deg": round(change, 1),
                        "dt_s": dt,
                        "change_rate_deg_s": round(rate, 2),
                        "threshold_rate_deg_s": config.max_heading_change_rate_deg_s,
                        "description": "Navigation anomaly: reported heading changed far faster than an ordinary turn.",
                    },
                    supporting_observation_ids=_ids(prev, cur),
                    source_kind=_provenance(prev, cur)[0],
                    source_kinds=_provenance(prev, cur)[1],
                )
            )
    return detections


def detect_course_discontinuity(
    points: list[dict], config: DetectorConfig | None = None
) -> list[Detection]:
    """Computed course reversal between consecutive segments, from
    positions alone (no reported heading needed)."""
    config = config or DetectorConfig()
    icao24 = points[0]["icao24"] if points else ""
    segments = segments_for_track(points)
    detections: list[Detection] = []
    for first, second in zip(segments, segments[1:]):
        if second.start_at - first.start_at > config.course_change_window_s:
            continue
        change = angular_diff_deg(first.bearing_deg, second.bearing_deg)
        if change > config.course_change_deg:
            detections.append(
                Detection(
                    rule="course_discontinuity",
                    severity="medium",
                    kind="anomaly",
                    icao24=icao24,
                    detected_at=second.end_at,
                    latitude=second.end_lat,
                    longitude=second.end_lon,
                    evidence={
                        "rule": "course_discontinuity",
                        "course_before_deg": round(first.bearing_deg, 1),
                        "course_after_deg": round(second.bearing_deg, 1),
                        "change_deg": round(change, 1),
                        "threshold_change_deg": config.course_change_deg,
                        "description": "Unusual movement: computed course reversed between consecutive segments.",
                    },
                    supporting_observation_ids=_ids_from_segment(points, second),
                    source_kind=_provenance(*_points_from_segment(points, second))[0],
                    source_kinds=_provenance(*_points_from_segment(points, second))[1],
                )
            )
    return detections


def detect_altitude_divergence(
    points: list[dict], config: DetectorConfig | None = None
) -> list[Detection]:
    """Barometric vs geometric altitude disagreement at one observation.
    This is a per-point detector: it needs two altitudes and a
    timestamp, not movement information, so it deliberately does NOT
    use clean_points — a stale flag or a missing position must not
    suppress an otherwise eligible divergence. The point's quality
    note is kept in the evidence as context. Abstains when either
    altitude is absent or unusable — absence is a data gap, not an
    anomaly."""
    config = config or DetectorConfig()
    ordered = sort_track(points)
    icao24 = points[0]["icao24"] if points else ""
    detections: list[Detection] = []
    for point in ordered:
        try:
            baro = point.get("baro_altitude_m")
            geo = point.get("geo_altitude_m")
            if baro is None or geo is None:
                continue
            baro_f, geo_f = float(baro), float(geo)
            detected_at = int(point["observed_at"])
            latitude = float(point["latitude"]) if point.get("latitude") is not None else None
            longitude = float(point["longitude"]) if point.get("longitude") is not None else None
        except (TypeError, ValueError, KeyError):
            # Genuinely invalid values are rejected, never coerced
            # into a detection.
            continue
        divergence = abs(geo_f - baro_f)
        if divergence > config.altitude_divergence_medium_m:
            evidence = {
                "rule": "altitude_divergence",
                "baro_altitude_m": baro_f,
                "geo_altitude_m": geo_f,
                "divergence_m": round(divergence, 1),
                "threshold_medium_m": config.altitude_divergence_medium_m,
                "threshold_high_m": config.altitude_divergence_high_m,
                "description": "Navigation anomaly: barometric and geometric altitudes disagree beyond the threshold.",
            }
            if point.get("quality_note") is not None:
                evidence["quality_note"] = point["quality_note"]
            detections.append(
                Detection(
                    rule="altitude_divergence",
                    severity=(
                        "high"
                        if divergence > config.altitude_divergence_high_m
                        else "medium"
                    ),
                    kind="anomaly",
                    icao24=icao24,
                    detected_at=detected_at,
                    latitude=latitude,
                    longitude=longitude,
                    evidence=evidence,
                    supporting_observation_ids=_ids(point),
                    source_kind=_provenance(point)[0],
                    source_kinds=_provenance(point)[1],
                )
            )
    return detections


def detect_data_quality(
    points: list[dict], config: DetectorConfig | None = None
) -> list[Detection]:
    """Data-quality findings, kept strictly separate from anomalies:
    missing positions and long gaps in the record. Neither is evidence
    of interference on its own; both are reasons to distrust a track
    segment and inputs to position-loss clustering."""
    config = config or DetectorConfig()
    ordered = sort_track(points)
    icao24 = points[0]["icao24"] if points else ""
    findings: list[Detection] = []
    for point in ordered:
        if point.get("latitude") is None or point.get("longitude") is None:
            findings.append(
                Detection(
                    rule="missing_position",
                    severity="low",
                    kind="data_quality",
                    icao24=icao24,
                    detected_at=int(point["observed_at"]),
                    latitude=None,
                    longitude=None,
                    evidence={
                        "rule": "missing_position",
                        "description": "Data-quality issue: observation has no reported position.",
                    },
                    supporting_observation_ids=_ids(point),
                    source_kind=_provenance(point)[0],
                    source_kinds=_provenance(point)[1],
                )
            )
    for prev, cur in zip(ordered, ordered[1:]):
        gap = int(cur["observed_at"]) - int(prev["observed_at"])
        if gap > config.track_gap_s:
            findings.append(
                Detection(
                    rule="track_gap",
                    severity="low",
                    kind="data_quality",
                    icao24=icao24,
                    detected_at=int(cur["observed_at"]),
                    latitude=(
                        float(cur["latitude"]) if cur.get("latitude") is not None else None
                    ),
                    longitude=(
                        float(cur["longitude"]) if cur.get("longitude") is not None else None
                    ),
                    evidence={
                        "rule": "track_gap",
                        "gap_s": gap,
                        "threshold_gap_s": config.track_gap_s,
                        "description": "Data-quality issue: long gap between consecutive stored observations.",
                    },
                    supporting_observation_ids=_ids(prev, cur),
                    source_kind=_provenance(prev, cur)[0],
                    source_kinds=_provenance(prev, cur)[1],
                )
            )
    return findings


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    if n % 2 == 1:
        return ordered[n // 2]
    return (ordered[n // 2 - 1] + ordered[n // 2]) / 2.0


def detect_common_mode_offsets(
    tracks: dict[str, list[dict]], config: DetectorConfig | None = None
) -> list[Detection]:
    """Cross-track detector: several aircraft jumping by approximately
    the same displacement vector in the same short window. A single
    teleport is one aircraft's problem; the same vector across
    aircraft is the coordinated-spoofing signature from the original
    idea ("3 aircraft simultaneously offset 380 km NE at 14:02").

    Only jump-like segments qualify (implied speed above the
    teleport threshold and displacement above the offset floor), so
    ordinary fast flight and slow drift do not fire this rule. Each
    participating aircraft gets its own Detection (the model carries
    one icao24 per detection) sharing the group evidence, so the
    detections cluster into a correlated event like any others.
    This fires IN ADDITION to teleport, it does not replace it.
    """
    import math

    config = config or DetectorConfig()
    jumps: list[dict] = []
    for icao24 in sorted(tracks):
        points_by_id = {p.get("id"): p for p in tracks[icao24]}
        for seg in segments_for_track(tracks[icao24]):
            if seg.dt_s <= 0 or seg.distance_km < config.common_mode_min_offset_km:
                continue
            if seg.speed_ms is None or seg.speed_ms <= config.max_speed_ms:
                continue
            bearing = math.radians(seg.bearing_deg)
            jumps.append(
                {
                    "icao24": icao24,
                    "segment": seg,
                    "east_km": seg.distance_km * math.sin(bearing),
                    "north_km": seg.distance_km * math.cos(bearing),
                    "start_point": points_by_id.get(seg.start_id),
                    "end_point": points_by_id.get(seg.end_id),
                }
            )
    groups: list[list[dict]] = []
    for jump in sorted(jumps, key=lambda j: (j["segment"].start_at, j["icao24"])):
        placed = False
        for group in groups:
            anchor = group[0]
            if abs(jump["segment"].start_at - anchor["segment"].start_at) > config.common_mode_window_s:
                continue
            direction_diff = angular_diff_deg(
                jump["segment"].bearing_deg, anchor["segment"].bearing_deg
            )
            if direction_diff > config.common_mode_direction_tol_deg:
                continue
            mag_diff = abs(jump["segment"].distance_km - anchor["segment"].distance_km) / max(
                jump["segment"].distance_km, anchor["segment"].distance_km
            )
            if mag_diff > config.common_mode_magnitude_tol:
                continue
            group.append(jump)
            placed = True
            break
        if not placed:
            groups.append([jump])
    findings: list[Detection] = []
    for group in groups:
        aircraft = sorted({j["icao24"] for j in group})
        if len(aircraft) < 2:
            continue
        east = _median([j["east_km"] for j in group])
        north = _median([j["north_km"] for j in group])
        magnitude = (east**2 + north**2) ** 0.5
        direction = (math.degrees(math.atan2(east, north)) + 360.0) % 360.0
        for jump in group:
            seg = jump["segment"]
            points = [p for p in (jump["start_point"], jump["end_point"]) if p is not None]
            kind, kinds = _provenance(*points)
            findings.append(
                Detection(
                    rule="common_mode_offset",
                    severity="high",
                    kind="anomaly",
                    icao24=jump["icao24"],
                    detected_at=seg.end_at,
                    latitude=seg.end_lat,
                    longitude=seg.end_lon,
                    evidence={
                        "rule": "common_mode_offset",
                        "aircraft": aircraft,
                        "shared_offset_east_km": round(east, 1),
                        "shared_offset_north_km": round(north, 1),
                        "shared_offset_magnitude_km": round(magnitude, 1),
                        "shared_offset_direction_deg": round(direction, 1),
                        "window_s": config.common_mode_window_s,
                        "min_offset_km": config.common_mode_min_offset_km,
                        "description": (
                            f"Coordinated-movement candidate: {len(aircraft)} aircraft "
                            f"jumped by approximately the same {magnitude:.0f} km vector "
                            "in the same window. Potential interference indicator; "
                            "investigate before drawing any conclusion."
                        ),
                    },
                    supporting_observation_ids=_ids(*points),
                    source_kind=kind,
                    source_kinds=kinds,
                )
            )
    return findings


def _circumcenter_km(
    p1: tuple[float, float], p2: tuple[float, float], p3: tuple[float, float]
) -> tuple[float, float, float] | None:
    """Circumcentre and radius (km) of three plane points, or None
    when they are collinear. Coordinates are equirectangular
    kilometres around the triple's mean latitude — a planar
    approximation, documented as such: it is adequate for a
    candidate pattern flag, not a geodetic survey."""
    import math

    mean_lat = math.radians((p1[0] + p2[0] + p3[0]) / 3.0)
    cos_lat = math.cos(mean_lat)
    r = 6371.0

    def to_xy(p: tuple[float, float]) -> tuple[float, float]:
        return (math.radians(p[1]) * cos_lat * r, math.radians(p[0]) * r)

    ax, ay = to_xy(p1)
    bx, by = to_xy(p2)
    cx, cy = to_xy(p3)
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        return None
    ux = (
        (ax * ax + ay * ay) * (by - cy)
        + (bx * bx + by * by) * (cy - ay)
        + (cx * cx + cy * cy) * (ay - by)
    ) / d
    uy = (
        (ax * ax + ay * ay) * (cx - bx)
        + (bx * bx + by * by) * (ax - cx)
        + (cx * cx + cy * cy) * (bx - ax)
    ) / d
    radius = math.hypot(ax - ux, ay - uy)
    # Back to lat/lon: invert the projection around the same mean latitude.
    centre_lat = math.degrees(uy / r)
    centre_lon = math.degrees(ux / (cos_lat * r))
    return (centre_lat, centre_lon, radius)


def detect_circle_spoof(
    tracks: dict[str, list[dict]], config: DetectorConfig | None = None
) -> list[Detection]:
    """Cross-track detector: several aircraft curving around a shared
    fitted centre — the "circle spoofing" pattern from the original
    idea, where many tracks curve around one phantom point.

    Per aircraft, consecutive point triples with a significant turn
    yield fitted circle centres; an aircraft qualifies when at
    least two of its centres agree within tolerance and share a
    turn direction. Aircraft whose fitted centres agree within
    tolerance, with the same direction, in the same window, form a
    group; groups of at least circle_min_aircraft aircraft fire one
    Detection per aircraft. The detection's position IS the fitted
    centre (the phantom point), stated as such in the evidence —
    it is not an aircraft position. Noisy positions make fitted
    centres noisy, which is why the tolerance is generous and the
    result is a candidate pattern flag, never a proof.
    """
    import math

    config = config or DetectorConfig()
    per_aircraft: list[dict] = []
    for icao24 in sorted(tracks):
        clean = [p for p in sort_track(tracks[icao24]) if p.get("latitude") is not None and p.get("longitude") is not None and p.get("quality_note") is None]
        triples: list[dict] = []
        for a, b, c in zip(clean, clean[1:], clean[2:]):
            if int(c["observed_at"]) - int(a["observed_at"]) > config.circle_window_s:
                continue
            b1 = bearing_deg(float(a["latitude"]), float(a["longitude"]), float(b["latitude"]), float(b["longitude"]))
            b2 = bearing_deg(float(b["latitude"]), float(b["longitude"]), float(c["latitude"]), float(c["longitude"]))
            turn = angular_diff_deg(b1, b2)
            if turn < config.circle_min_turn_deg or turn > 180.0 - config.circle_min_turn_deg:
                continue
            fit = _circumcenter_km(
                (float(a["latitude"]), float(a["longitude"])),
                (float(b["latitude"]), float(b["longitude"])),
                (float(c["latitude"]), float(c["longitude"])),
            )
            if fit is None or fit[2] > config.circle_radius_max_km or fit[2] < 1.0:
                continue
            # Turn direction: sign of the cross product in the local
            # east/north plane. Negative = clockwise (right turn).
            mean_lat = math.radians((float(a["latitude"]) + float(c["latitude"])) / 2.0)
            x1 = (float(b["longitude"]) - float(a["longitude"])) * math.cos(mean_lat)
            y1 = float(b["latitude"]) - float(a["latitude"])
            x2 = (float(c["longitude"]) - float(b["longitude"])) * math.cos(mean_lat)
            y2 = float(c["latitude"]) - float(b["latitude"])
            direction = "clockwise" if (x1 * y2 - y1 * x2) < 0 else "counterclockwise"
            triples.append(
                {
                    "centre": (fit[0], fit[1]),
                    "radius_km": fit[2],
                    "direction": direction,
                    "mid_at": (int(a["observed_at"]) + int(c["observed_at"])) // 2,
                    "points": [a, b, c],
                }
            )
        if len(triples) < 2:
            continue
        centre_lat = _median([t["centre"][0] for t in triples])
        centre_lon = _median([t["centre"][1] for t in triples])
        kept = [
            t
            for t in triples
            if haversine_km(centre_lat, centre_lon, t["centre"][0], t["centre"][1])
            <= config.circle_center_tol_km
        ]
        if len(kept) < 2:
            continue
        direction = max(
            {"clockwise", "counterclockwise"},
            key=lambda d: sum(1 for t in kept if t["direction"] == d),
        )
        kept = [t for t in kept if t["direction"] == direction]
        if len(kept) < 2:
            continue
        per_aircraft.append(
            {
                "icao24": icao24,
                "centre": (centre_lat, centre_lon),
                "radius_km": _median([t["radius_km"] for t in kept]),
                "direction": direction,
                "mid_at": _median([float(t["mid_at"]) for t in kept]),
                "triples": kept,
            }
        )
    groups: list[list[dict]] = []
    for candidate in sorted(per_aircraft, key=lambda c: (c["mid_at"], c["icao24"])):
        placed = False
        for group in groups:
            anchor = group[0]
            if abs(candidate["mid_at"] - anchor["mid_at"]) > config.circle_window_s:
                continue
            if candidate["direction"] != anchor["direction"]:
                continue
            if (
                haversine_km(
                    candidate["centre"][0],
                    candidate["centre"][1],
                    anchor["centre"][0],
                    anchor["centre"][1],
                )
                > config.circle_center_tol_km
            ):
                continue
            group.append(candidate)
            placed = True
            break
        if not placed:
            groups.append([candidate])
    findings: list[Detection] = []
    for group in groups:
        if len(group) < config.circle_min_aircraft:
            continue
        aircraft = sorted(c["icao24"] for c in group)
        centre_lat = _median([c["centre"][0] for c in group])
        centre_lon = _median([c["centre"][1] for c in group])
        radius = _median([c["radius_km"] for c in group])
        direction = group[0]["direction"]
        for candidate in group:
            seen: dict[int, dict] = {}
            for triple in candidate["triples"]:
                for p in triple["points"]:
                    if p.get("id") is not None:
                        seen[int(p["id"])] = p
            points = list(seen.values())
            kind, kinds = _provenance(*points)
            findings.append(
                Detection(
                    rule="circle_spoof_pattern",
                    severity="high",
                    kind="anomaly",
                    icao24=candidate["icao24"],
                    detected_at=int(candidate["mid_at"]),
                    latitude=centre_lat,
                    longitude=centre_lon,
                    evidence={
                        "rule": "circle_spoof_pattern",
                        "aircraft": aircraft,
                        "centre_lat": round(centre_lat, 4),
                        "centre_lon": round(centre_lon, 4),
                        "radius_km": round(radius, 1),
                        "turn_direction": direction,
                        "position_is_fitted_centre": True,
                        "description": (
                            f"Circle-spoofing candidate: {len(aircraft)} aircraft "
                            f"curving around a shared fitted centre near "
                            f"({centre_lat:.2f}, {centre_lon:.2f}). Potential "
                            "interference indicator; investigate before drawing "
                            "any conclusion."
                        ),
                    },
                    supporting_observation_ids=[int(p["id"]) for p in points],
                    source_kind=kind,
                    source_kinds=kinds,
                )
            )
    return findings


def run_track_detectors(
    points: list[dict], config: DetectorConfig | None = None
) -> list[Detection]:
    """All detectors for one aircraft's observations, time-ordered."""
    config = config or DetectorConfig()
    detections: list[Detection] = []
    for detector in (
        detect_teleport,
        detect_acceleration,
        detect_heading_change,
        detect_course_discontinuity,
        detect_altitude_divergence,
        detect_data_quality,
    ):
        detections.extend(detector(points, config))
    detections.sort(key=lambda d: (d.detected_at, d.rule))
    return detections


def run_all_detectors(
    observations: list[dict], config: DetectorConfig | None = None
) -> list[Detection]:
    """Detectors over many aircraft. Records without an icao24 are
    skipped defensively (the database enforces icao24 NOT NULL)."""
    config = config or DetectorConfig()
    by_aircraft: dict[str, list[dict]] = {}
    for observation in observations:
        if not observation.get("icao24"):
            continue
        by_aircraft.setdefault(str(observation["icao24"]), []).append(observation)
    detections: list[Detection] = []
    for icao24 in sorted(by_aircraft):
        detections.extend(run_track_detectors(by_aircraft[icao24], config))
    # Cross-track patterns run on the same tracks, after the
    # per-track detectors: they interpret relationships between
    # aircraft, not single tracks.
    detections.extend(detect_common_mode_offsets(by_aircraft, config))
    detections.extend(detect_circle_spoof(by_aircraft, config))
    detections.sort(key=lambda d: (d.detected_at, d.icao24, d.rule))
    return detections
