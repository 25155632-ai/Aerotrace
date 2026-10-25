"""Detectors: normal, suspicious, missing, irregular, false-positive, invalid."""

import pytest

from app.detectors.detectors import (
    DetectorConfig,
    detect_altitude_divergence,
    detect_course_discontinuity,
    detect_data_quality,
    detect_heading_change,
    detect_teleport,
    detect_acceleration,
    run_all_detectors,
    run_track_detectors,
)

BASE = {"icao24": "abc123", "source_kind": "simulated", "quality_note": None}


def point(id_, at, lat, lon, **extra):
    record = {
        **BASE,
        "id": id_,
        "observed_at": at,
        "latitude": lat,
        "longitude": lon,
        "baro_altitude_m": 10000.0,
        "geo_altitude_m": 10000.0,
        "velocity_ms": 250.0,
        "heading_deg": 45.0,
    }
    record.update(extra)
    return record


def straight_track():
    # Steady north-east movement at ~386 m/s: fast but below the
    # 500 m/s plausibility threshold, so it must not flag.
    return [
        point(1, 100, 19.07, 72.87),
        point(2, 110, 19.09, 72.90),
        point(3, 120, 19.11, 72.93),
    ]


def test_normal_straight_track_flags_nothing():
    assert run_track_detectors(straight_track()) == []


def test_ordinary_turn_and_modest_acceleration_do_not_flag():
    # Gradual heading change (3 deg per 10 s) and a gentle speed rise.
    track = [
        point(1, 100, 19.0, 72.0, heading_deg=0.0, velocity_ms=200.0),
        point(2, 110, 19.01, 72.0, heading_deg=3.0, velocity_ms=205.0),
        point(3, 120, 19.02, 72.0, heading_deg=6.0, velocity_ms=210.0),
    ]
    assert run_track_detectors(track) == []


def test_small_position_noise_does_not_flag():
    # One point offset by ~500 m from an otherwise straight track:
    # segment speeds stay well below threshold.
    track = [
        point(1, 100, 19.0, 72.0, heading_deg=90.0),
        point(2, 110, 19.0, 72.01, heading_deg=90.0),
        point(3, 120, 19.0045, 72.02, heading_deg=90.0),
        point(4, 130, 19.0, 72.03, heading_deg=90.0),
    ]
    rules = {d.rule for d in run_track_detectors(track)}
    assert "teleport" not in rules


def test_teleport_flags_with_evidence_and_supporting_ids():
    track = [
        point(1, 100, 19.0, 72.8),
        point(2, 110, 19.01, 72.81),
        point(3, 120, 25.0, 80.0),
    ]
    detections = detect_teleport(track)
    assert len(detections) == 1
    detection = detections[0]
    assert detection.rule == "teleport"
    assert detection.severity == "high"
    assert detection.kind == "anomaly"
    assert detection.detected_at == 120
    assert detection.supporting_observation_ids == [2, 3]
    assert detection.evidence["implied_speed_ms"] > 500.0
    assert detection.evidence["threshold_speed_ms"] == 500.0


def test_timestamp_collision_flags_same_second_different_position():
    track = [
        point(1, 100, 19.0, 72.0),
        point(2, 100, 20.0, 73.0),
    ]
    detections = detect_teleport(track)
    assert [d.rule for d in detections] == ["timestamp_collision"]
    assert detections[0].severity == "high"


def test_implausible_acceleration_flags():
    # Segment speeds 100 m/s then 300 m/s over a 10 s midpoint gap:
    # acceleration 20 m/s^2, above the 10 threshold.
    track = [
        point(1, 100, 0.0, 0.0, heading_deg=None),
        point(2, 110, 0.0, 0.009, heading_deg=None),  # ~1000 m
        point(3, 120, 0.0, 0.036, heading_deg=None),  # ~3000 m
    ]
    detections = detect_acceleration(track)
    assert len(detections) == 1
    assert detections[0].evidence["acceleration_ms2"] == pytest.approx(20.0, abs=0.1)


def test_sharp_heading_change_flags_but_absent_headings_abstain():
    # 180 degrees in 5 s = 36 deg/s, above the 20 deg/s threshold.
    track = [
        point(1, 100, 19.0, 72.0, heading_deg=0.0, velocity_ms=200.0),
        point(2, 105, 19.01, 72.0, heading_deg=180.0, velocity_ms=200.0),
    ]
    assert [d.rule for d in detect_heading_change(track)] == ["sharp_heading_change"]
    no_heading = [
        point(1, 100, 19.0, 72.0, heading_deg=None),
        point(2, 110, 19.01, 72.0, heading_deg=None),
    ]
    assert detect_heading_change(no_heading) == []


def test_course_discontinuity_from_positions_alone():
    # East 1 km, then west 1 km: course reverses 180 degrees.
    track = [
        point(1, 100, 0.0, 0.0, heading_deg=None),
        point(2, 110, 0.0, 0.009, heading_deg=None),
        point(3, 120, 0.0, 0.0, heading_deg=None),
    ]
    detections = detect_course_discontinuity(track)
    assert len(detections) == 1
    assert detections[0].evidence["change_deg"] == 180.0


def test_altitude_divergence_thresholds_and_missing_abstention():
    track = [
        point(1, 100, 19.0, 72.0, baro_altitude_m=10000.0, geo_altitude_m=10600.0),
        point(2, 110, 19.01, 72.01, baro_altitude_m=10000.0, geo_altitude_m=11800.0),
        point(3, 120, 19.02, 72.02, baro_altitude_m=10000.0, geo_altitude_m=None),
        point(4, 130, 19.03, 72.03, baro_altitude_m=10000.0, geo_altitude_m=10400.0),
    ]
    detections = detect_altitude_divergence(track)
    assert [(d.severity, d.detected_at) for d in detections] == [
        ("medium", 100),
        ("high", 110),
    ]


def test_data_quality_gap_and_missing_position():
    track = [
        point(1, 100, 19.0, 72.0),
        point(2, 800, 19.01, 72.01),  # 700 s gap
        {"id": 3, "icao24": "abc123", "observed_at": 810, "latitude": None,
         "longitude": None, "source_kind": "simulated", "quality_note": "missing_coordinates"},
    ]
    findings = detect_data_quality(track)
    assert {f.rule for f in findings} == {"track_gap", "missing_position"}
    assert all(f.kind == "data_quality" and f.severity == "low" for f in findings)


def test_stale_far_point_is_excluded_from_movement():
    track = [
        point(1, 100, 19.07, 72.87),
        point(2, 110, 25.0, 80.0, quality_note="stale"),
        point(3, 120, 19.09, 72.90),
    ]
    movement_rules = {
        d.rule
        for d in run_track_detectors(track)
        if d.kind == "anomaly"
    }
    assert movement_rules == set()


def test_irregular_sampling_and_invalid_inputs_do_not_crash():
    # Irregular gaps (10 s, then 300 s) at a constant plausible speed.
    track = [
        point(1, 100, 0.0, 0.0),
        point(2, 110, 0.0, 0.009),
        point(3, 410, 0.0, 0.279),
    ]
    assert run_track_detectors(track) == []
    assert run_track_detectors([]) == []
    assert run_track_detectors([point(1, 100, 19.0, 72.0)]) == []
    # Records without an aircraft are skipped, not fatal.
    assert run_all_detectors([{"observed_at": 100, "latitude": 0.0, "longitude": 0.0}]) == []
    # Custom thresholds are honoured: a 386 m/s track flags at 300.
    config = DetectorConfig(max_speed_ms=300.0)
    assert any(d.rule == "teleport" for d in run_track_detectors(straight_track(), config))
