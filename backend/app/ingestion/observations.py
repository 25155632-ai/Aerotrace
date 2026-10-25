"""Validated aircraft-observation model and normalisation.

One raw observation is one dict, whatever its origin (demo dataset,
CSV-style import, or the OpenSky adapter, which already maps its array
format into this shape). This module is the only place that decides
what an observation *means*; adapters never validate, and storage
never normalises.

Normalisation rules (all documented here, none silent):
- Timestamps become integer epoch seconds (UTC). Accepted forms: epoch
  seconds, epoch milliseconds (values >= 1e12 are treated as ms),
  numeric strings in either unit, and ISO 8601 strings *with* a
  timezone (a trailing Z counts). Naive ISO strings are rejected:
  guessing a timezone for aviation data would be a silent error.
  Epoch values outside 1970–2100 are rejected.
- Coordinates are decimal degrees only. DMS strings are not an ADS-B
  format and are rejected, not guessed at. Numeric strings are parsed;
  an empty string means "missing", not zero.
- Latitude must be in [-90, 90], longitude in [-180, 180]. Out-of-range
  values are rejected, never wrapped or clamped: wrapping 181° to -179°
  would invent a position on the other side of the planet.
- Altitudes: canonical fields are metres. A `_ft` alias, when the metre
  field is absent, is converted (x 0.3048). If both are present, the
  metre field wins and the alias is ignored.
- Velocity: canonical fields are m/s. A `_kt` alias converts
  (x 0.514444) when the m/s field is absent.
- Heading is normalised modulo 360 (360° becomes 0°); a non-finite
  heading is rejected.
- `icao24` is a 6-character lowercase hex string after stripping.
  Anything else cannot be attributed to an aircraft and is rejected.
- Optional fields (altitude, velocity, heading, callsign) may be
  missing; missing is stored as NULL and is *not* an error. Missing
  latitude or longitude is handled by the pipeline as a data-quality
  record (see pipeline.py), not by this module.
- `received_at` defaults to `observed_at` when absent. That is the
  replay/demo semantic: a recorded track is not "stale" just because
  it is old. Live adapters must pass wall-clock receipt time so the
  pipeline's staleness check means something.
- Unknown fields are ignored. Rejecting them would make the pipeline
  brittle to harmless ADS-B variants; inventing meaning for them would
  be worse.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime

ICAO24_RE = re.compile(r"^[0-9a-f]{6}$")
_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?([eE][+-]?\d+)?$")

MAX_EPOCH_SECONDS = 4102444800  # 2100-01-01 UTC
MS_THRESHOLD = 1_000_000_000_000  # >= this, a number is treated as epoch ms
FEET_TO_METRES = 0.3048
KNOTS_TO_MS = 0.514444


class ObservationError(ValueError):
    """A raw observation that cannot be normalised. Carries the reason
    so the pipeline can report it per record instead of failing a batch."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def parse_timestamp(value: object, field: str = "observed_at") -> int:
    if _is_number(value):
        number = float(value)  # type: ignore[arg-type]
    elif isinstance(value, str):
        text = value.strip()
        if _NUMERIC_RE.match(text):
            number = float(text)
        else:
            iso = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
            try:
                parsed = datetime.fromisoformat(iso)
            except ValueError as exc:
                raise ObservationError(
                    f"{field}: unparseable timestamp {value!r}"
                ) from exc
            if parsed.tzinfo is None:
                raise ObservationError(
                    f"{field}: naive ISO timestamp {value!r} has no timezone"
                )
            return int(parsed.timestamp())
    else:
        raise ObservationError(f"{field}: unsupported timestamp type {type(value).__name__}")

    if not math.isfinite(number):
        raise ObservationError(f"{field}: non-finite timestamp")
    seconds = int(number / 1000) if abs(number) >= MS_THRESHOLD else int(number)
    if seconds < 0 or seconds > MAX_EPOCH_SECONDS:
        raise ObservationError(f"{field}: timestamp out of range: {seconds}")
    return seconds


def parse_optional_number(value: object, field: str) -> float | None:
    """Parse an optional numeric field. Empty string counts as missing."""
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    if _is_number(value):
        number = float(value)  # type: ignore[arg-type]
    elif isinstance(value, str) and _NUMERIC_RE.match(value.strip()):
        number = float(value.strip())
    else:
        raise ObservationError(f"{field}: not a number: {value!r}")
    if not math.isfinite(number):
        raise ObservationError(f"{field}: non-finite value")
    return number


def _range_checked(value: float | None, field: str, low: float, high: float) -> float | None:
    if value is None:
        return None
    if value < low or value > high:
        raise ObservationError(f"{field}: {value} outside [{low}, {high}]")
    return value


@dataclass(frozen=True)
class NormalizedObservation:
    icao24: str
    observed_at: int
    latitude: float | None
    longitude: float | None
    baro_altitude_m: float | None
    geo_altitude_m: float | None
    velocity_ms: float | None
    heading_deg: float | None
    callsign: str | None
    source: str
    source_kind: str  # 'real' | 'simulated'
    received_at: int


def _altitude(raw: dict, metre_key: str, feet_key: str) -> float | None:
    if raw.get(metre_key) is not None:
        value = parse_optional_number(raw.get(metre_key), metre_key)
    elif raw.get(feet_key) is not None:
        feet = parse_optional_number(raw.get(feet_key), feet_key)
        value = None if feet is None else feet * FEET_TO_METRES
    else:
        value = None
    return _range_checked(value, metre_key, -1000.0, 25000.0)


def _velocity(raw: dict) -> float | None:
    if raw.get("velocity_ms") is not None:
        value = parse_optional_number(raw.get("velocity_ms"), "velocity_ms")
    elif raw.get("velocity_kt") is not None:
        knots = parse_optional_number(raw.get("velocity_kt"), "velocity_kt")
        value = None if knots is None else knots * KNOTS_TO_MS
    else:
        value = None
    return _range_checked(value, "velocity_ms", 0.0, 1000.0)


def normalize_observation(raw: object) -> NormalizedObservation:
    if not isinstance(raw, dict):
        raise ObservationError(f"observation is not an object: {type(raw).__name__}")

    icao_raw = raw.get("icao24")
    if not isinstance(icao_raw, str):
        raise ObservationError("icao24: missing or not a string")
    icao24 = icao_raw.strip().lower()
    if not ICAO24_RE.match(icao24):
        raise ObservationError(f"icao24: not a 6-character hex address: {icao_raw!r}")

    if raw.get("observed_at") is None:
        raise ObservationError("observed_at: missing")
    observed_at = parse_timestamp(raw.get("observed_at"), "observed_at")

    source_raw = raw.get("source")
    if not isinstance(source_raw, str) or not source_raw.strip():
        raise ObservationError("source: missing or empty")
    source = source_raw.strip()[:128]

    kind_raw = raw.get("source_kind")
    if not isinstance(kind_raw, str):
        raise ObservationError("source_kind: missing or not a string")
    source_kind = kind_raw.strip().lower()
    if source_kind not in ("real", "simulated"):
        raise ObservationError(f"source_kind: must be 'real' or 'simulated', got {kind_raw!r}")

    received_at = (
        parse_timestamp(raw.get("received_at"), "received_at")
        if raw.get("received_at") is not None
        else observed_at
    )

    latitude = _range_checked(
        parse_optional_number(raw.get("latitude"), "latitude"), "latitude", -90.0, 90.0
    )
    longitude = _range_checked(
        parse_optional_number(raw.get("longitude"), "longitude"),
        "longitude",
        -180.0,
        180.0,
    )

    heading_raw = parse_optional_number(raw.get("heading_deg"), "heading_deg")
    heading_deg = None if heading_raw is None else heading_raw % 360.0

    callsign_raw = raw.get("callsign")
    if callsign_raw is None or (isinstance(callsign_raw, str) and callsign_raw.strip() == ""):
        callsign = None
    elif isinstance(callsign_raw, str):
        # Charset restriction, not just truncation: callsigns flow into
        # contexts where arbitrary characters are unsafe (see the
        # Leaflet tooltip note in MapView.tsx), and real ADS-B
        # callsigns use only letters, digits, spaces, and hyphens.
        # Anything else is rejected, never sanitised into a different
        # value — silently rewriting an identifier would corrupt it.
        callsign = callsign_raw.strip().upper()
        if len(callsign) > 8 or not re.fullmatch(r"[A-Z0-9 _-]+", callsign):
            raise ObservationError(f"callsign: invalid characters or length: {callsign_raw!r}")
    else:
        raise ObservationError("callsign: not a string")

    return NormalizedObservation(
        icao24=icao24,
        observed_at=observed_at,
        latitude=latitude,
        longitude=longitude,
        baro_altitude_m=_altitude(raw, "baro_altitude_m", "baro_altitude_ft"),
        geo_altitude_m=_altitude(raw, "geo_altitude_m", "geo_altitude_ft"),
        velocity_ms=_velocity(raw),
        heading_deg=heading_deg,
        callsign=callsign,
        source=source,
        source_kind=source_kind,
        received_at=received_at,
    )
