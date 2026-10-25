"""Observation normalisation: timestamps, units, ranges, rejections."""

import pytest

from app.ingestion.observations import ObservationError, normalize_observation


def base(**overrides):
    record = {
        "icao24": "abc123",
        "observed_at": 1700000000,
        "latitude": 19.07,
        "longitude": 72.87,
        "source": "test",
        "source_kind": "simulated",
    }
    record.update(overrides)
    return record


def test_epoch_seconds_ms_numeric_string_and_iso_all_normalise():
    assert normalize_observation(base(observed_at=1700000000)).observed_at == 1700000000
    assert normalize_observation(base(observed_at=1700000000000)).observed_at == 1700000000
    assert normalize_observation(base(observed_at="1700000000")).observed_at == 1700000000
    assert (
        normalize_observation(base(observed_at="2023-11-14T22:13:20Z")).observed_at
        == 1700000000
    )
    assert (
        normalize_observation(base(observed_at="2023-11-14T23:13:20+01:00")).observed_at
        == 1700000000
    )


def test_naive_iso_and_bad_timestamps_rejected():
    for bad in ("2023-11-14T22:13:20", "not-a-time", None, True, float("nan")):
        with pytest.raises(ObservationError):
            normalize_observation(base(observed_at=bad))


def test_icao24_must_be_six_hex_chars():
    assert normalize_observation(base(icao24="ABC123")).icao24 == "abc123"
    for bad in ("abc12", "abc1234", "xyz123", "", 123456, None):
        with pytest.raises(ObservationError):
            normalize_observation(base(icao24=bad))


def test_coordinate_ranges_rejected_not_wrapped():
    with pytest.raises(ObservationError):
        normalize_observation(base(latitude=91.0))
    with pytest.raises(ObservationError):
        normalize_observation(base(longitude=181.0))
    with pytest.raises(ObservationError):
        normalize_observation(base(latitude="19°04'12\"N"))


def test_unit_aliases_convert_and_metres_win():
    obs = normalize_observation(base(baro_altitude_ft=10000, velocity_kt=100))
    assert obs.baro_altitude_m == pytest.approx(3048.0)
    assert obs.velocity_ms == pytest.approx(51.4444)
    obs2 = normalize_observation(
        base(baro_altitude_m=5000.0, baro_altitude_ft=10000, velocity_ms=100.0, velocity_kt=200)
    )
    assert obs2.baro_altitude_m == 5000.0
    assert obs2.velocity_ms == 100.0


def test_heading_normalises_modulo_360():
    assert normalize_observation(base(heading_deg=360)).heading_deg == 0.0
    assert normalize_observation(base(heading_deg=370)).heading_deg == 10.0


def test_negative_velocity_and_bad_source_kind_rejected():
    with pytest.raises(ObservationError):
        normalize_observation(base(velocity_ms=-5))
    with pytest.raises(ObservationError):
        normalize_observation(base(source_kind="maybe"))


def test_missing_optional_fields_are_none_not_errors():
    obs = normalize_observation(base())
    assert obs.baro_altitude_m is None
    assert obs.velocity_ms is None
    assert obs.heading_deg is None
    assert obs.callsign is None
    # Empty string counts as missing for optional numeric fields.
    assert normalize_observation(base(latitude="")).latitude is None


def test_received_at_defaults_to_observed_at():
    obs = normalize_observation(base())
    assert obs.received_at == 1700000000
    obs2 = normalize_observation(base(received_at=1700000100))
    assert obs2.received_at == 1700000100
