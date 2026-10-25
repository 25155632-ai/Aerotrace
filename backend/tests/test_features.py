"""Feature extraction: haversine, bearing, angular difference, segments."""

import pytest

from app.detectors.features import (
    angular_diff_deg,
    bearing_deg,
    haversine_km,
    segments_for_track,
)


def test_haversine_and_bearing_known_values():
    # One degree of longitude at the equator is about 111.19 km.
    assert haversine_km(0.0, 0.0, 0.0, 1.0) == pytest.approx(111.19, abs=0.1)
    assert bearing_deg(0.0, 0.0, 1.0, 0.0) == pytest.approx(0.0)
    assert bearing_deg(0.0, 0.0, 0.0, 1.0) == pytest.approx(90.0)
    assert bearing_deg(0.0, 0.0, -1.0, 0.0) == pytest.approx(180.0)


def test_angular_diff_wraps_around_north():
    assert angular_diff_deg(350.0, 10.0) == pytest.approx(20.0)
    assert angular_diff_deg(10.0, 350.0) == pytest.approx(20.0)
    assert angular_diff_deg(0.0, 180.0) == pytest.approx(180.0)


def test_segments_use_each_segments_own_elapsed_time():
    points = [
        {"id": 1, "icao24": "abc123", "observed_at": 100, "latitude": 0.0, "longitude": 0.0, "quality_note": None},
        {"id": 2, "icao24": "abc123", "observed_at": 110, "latitude": 0.0, "longitude": 0.01, "quality_note": None},
        {"id": 3, "icao24": "abc123", "observed_at": 200, "latitude": 0.0, "longitude": 0.02, "quality_note": None},
    ]
    segments = segments_for_track(points)
    assert len(segments) == 2
    assert segments[0].speed_ms == pytest.approx(111.19, abs=0.5)
    # Second segment covers the same distance in 90 s, not 10 s.
    assert segments[1].speed_ms == pytest.approx(12.35, abs=0.5)


def test_segments_exclude_stale_and_positionless_points():
    points = [
        {"id": 1, "icao24": "abc123", "observed_at": 100, "latitude": 0.0, "longitude": 0.0, "quality_note": None},
        {"id": 2, "icao24": "abc123", "observed_at": 110, "latitude": 5.0, "longitude": 5.0, "quality_note": "stale"},
        {"id": 3, "icao24": "abc123", "observed_at": 120, "latitude": None, "longitude": None, "quality_note": "missing_coordinates"},
        {"id": 4, "icao24": "abc123", "observed_at": 130, "latitude": 0.0, "longitude": 0.01, "quality_note": None},
    ]
    segments = segments_for_track(points)
    assert len(segments) == 1
    assert segments[0].start_id == 1
    assert segments[0].end_id == 4
