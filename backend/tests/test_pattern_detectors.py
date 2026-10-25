"""Cross-track pattern detectors: common-mode offsets and circle-spoof
patterns. Positive cases use coordinated synthetic tracks; negative
cases prove ordinary flight, mismatched vectors, and straight tracks
do not fire. All data here is synthetic and labelled simulated."""

import math

from app.detectors.detectors import (
    detect_circle_spoof,
    detect_common_mode_offsets,
)

BASE = {"source_kind": "simulated", "quality_note": None}


def point(id_, icao24, at, lat, lon):
    return {
        **BASE,
        "id": id_,
        "icao24": icao24,
        "observed_at": at,
        "latitude": lat,
        "longitude": lon,
        "baro_altitude_m": 10000.0,
        "geo_altitude_m": 10000.0,
        "velocity_ms": 250.0,
        "heading_deg": 45.0,
    }


def eastward_track(icao24, start_id, lat, lon, jump=None):
    """5 eastward points, 10 s apart (~2.5 km per step at lat 20).
    jump=(at_index, dlat, dlon): at that step the track is displaced
    by the given vector instead of stepping east."""
    track = []
    cur_lat, cur_lon = lat, lon
    for i in range(5):
        if jump is not None and i == jump[0]:
            cur_lat += jump[1]
            cur_lon += jump[2]
        else:
            cur_lon += 0.024
        track.append(point(start_id + i, icao24, 1000 + i * 10, cur_lat, cur_lon))
    return track


def test_common_mode_offset_fires_for_shared_vector():
    tracks = {
        "aaa111": eastward_track("aaa111", 1, 20.0, 74.0, jump=(3, 2.0, 1.0)),
        "bbb222": eastward_track("bbb222", 11, 21.0, 75.0, jump=(3, 2.0, 1.0)),
        "ccc333": eastward_track("ccc333", 21, 22.0, 76.0, jump=(3, 2.0, 1.0)),
    }
    findings = detect_common_mode_offsets(tracks)
    assert len(findings) == 3
    assert all(f.rule == "common_mode_offset" for f in findings)
    assert all(f.severity == "high" for f in findings)
    assert sorted(f.icao24 for f in findings) == ["aaa111", "bbb222", "ccc333"]
    evidence = findings[0].evidence
    assert evidence["aircraft"] == ["aaa111", "bbb222", "ccc333"]
    assert evidence["shared_offset_magnitude_km"] > 200
    # Each detection is supported by exactly that aircraft's jump segment.
    assert len(findings[0].supporting_observation_ids) == 2


def test_common_mode_offset_ignores_mismatched_vectors():
    tracks = {
        "aaa111": eastward_track("aaa111", 1, 20.0, 74.0, jump=(3, 2.0, 1.0)),
        "bbb222": eastward_track("bbb222", 11, 21.0, 75.0, jump=(3, -2.0, 1.0)),
    }
    assert detect_common_mode_offsets(tracks) == []


def test_common_mode_offset_ignores_single_aircraft_jump():
    tracks = {
        "aaa111": eastward_track("aaa111", 1, 20.0, 74.0, jump=(3, 2.0, 1.0)),
        "bbb222": eastward_track("bbb222", 11, 21.0, 75.0),
    }
    assert detect_common_mode_offsets(tracks) == []


def test_common_mode_offset_ignores_small_offsets():
    # A 10 km step is ordinary movement, not a spoof-scale offset.
    tracks = {
        "aaa111": eastward_track("aaa111", 1, 20.0, 74.0, jump=(3, 0.05, 0.05)),
        "bbb222": eastward_track("bbb222", 11, 21.0, 75.0, jump=(3, 0.05, 0.05)),
    }
    assert detect_common_mode_offsets(tracks) == []


def circle_track(icao24, start_id, centre_lat, centre_lon, radius_deg, phase_deg, clockwise=True):
    """Points on a circle around a centre, 10 s apart, 30 degrees of
    arc per step. Radius in degrees; the fitted centre should come
    back near (centre_lat, centre_lon) regardless of phase."""
    track = []
    direction = -1 if clockwise else 1
    for i in range(6):
        angle = math.radians(phase_deg + direction * i * 30.0)
        lat = centre_lat + radius_deg * math.sin(angle)
        lon = centre_lon + radius_deg * math.cos(angle)
        track.append(point(start_id + i, icao24, 1000 + i * 10, lat, lon))
    return track


def test_circle_spoof_fires_for_shared_centre():
    tracks = {
        "aaa111": circle_track("aaa111", 1, 20.0, 75.0, 0.5, 0.0),
        "bbb222": circle_track("bbb222", 11, 20.0, 75.0, 0.8, 120.0),
        "ccc333": circle_track("ccc333", 21, 20.0, 75.0, 0.5, 240.0),
    }
    findings = detect_circle_spoof(tracks)
    assert len(findings) == 3
    assert all(f.rule == "circle_spoof_pattern" for f in findings)
    evidence = findings[0].evidence
    assert evidence["aircraft"] == ["aaa111", "bbb222", "ccc333"]
    assert evidence["position_is_fitted_centre"] is True
    # Fitted centre must be near the true shared centre.
    assert abs(evidence["centre_lat"] - 20.0) < 0.5
    assert abs(evidence["centre_lon"] - 75.0) < 0.5


def test_circle_spoof_ignores_different_centres():
    tracks = {
        "aaa111": circle_track("aaa111", 1, 20.0, 75.0, 0.5, 0.0),
        "bbb222": circle_track("bbb222", 11, 25.0, 80.0, 0.5, 0.0),
        "ccc333": circle_track("ccc333", 21, 30.0, 85.0, 0.5, 0.0),
    }
    assert detect_circle_spoof(tracks) == []


def test_circle_spoof_ignores_straight_tracks():
    tracks = {
        "aaa111": eastward_track("aaa111", 1, 20.0, 74.0),
        "bbb222": eastward_track("bbb222", 11, 21.0, 75.0),
        "ccc333": eastward_track("ccc333", 21, 22.0, 76.0),
    }
    assert detect_circle_spoof(tracks) == []


def test_circle_spoof_needs_three_aircraft():
    tracks = {
        "aaa111": circle_track("aaa111", 1, 20.0, 75.0, 0.5, 0.0),
        "bbb222": circle_track("bbb222", 11, 20.0, 75.0, 0.5, 120.0),
    }
    assert detect_circle_spoof(tracks) == []
