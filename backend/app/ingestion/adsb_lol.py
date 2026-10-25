"""adsb.lol v2 nearby-aircraft adapter.

The endpoint is intentionally explicit about its centre and radius. It does
not claim coverage freshness beyond the provider response timestamp.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from ..config import ADSB_LOL_BASE_URL


@dataclass
class NearbyResult:
    records: list[dict]
    response_time: int | None


def _record(item: dict, response_time: int) -> dict | None:
    icao24 = item.get("hex") or item.get("icao24")
    if not icao24:
        return None
    observed_at = item.get("now") or response_time - int(item.get("seen_pos") or item.get("seen") or 0)
    return {
        "icao24": str(icao24).strip().lower(),
        "observed_at": int(observed_at),
        "latitude": item.get("lat"),
        "longitude": item.get("lon"),
        "baro_altitude_m": item.get("alt_baro") if isinstance(item.get("alt_baro"), (int, float)) else None,
        "geo_altitude_m": item.get("alt_geom") if isinstance(item.get("alt_geom"), (int, float)) else None,
        "velocity_ms": float(item["gs"]) * 0.514444 if isinstance(item.get("gs"), (int, float)) else None,
        "heading_deg": item.get("track"),
        "callsign": str(item["flight"]).strip() if item.get("flight") else None,
        "source": "adsb.lol",
        "source_kind": "real",
        "received_at": response_time,
    }


def fetch_nearby(latitude: float, longitude: float, radius_nm: int, *, timeout_seconds: float = 10.0, transport=None) -> NearbyResult:
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180 and 1 <= radius_nm <= 250):
        raise ValueError("invalid adsb.lol nearby query")
    client_kwargs = {"timeout": timeout_seconds}
    if transport is not None:
        client_kwargs["transport"] = transport
    with httpx.Client(**client_kwargs) as client:
        response = client.get(f"{ADSB_LOL_BASE_URL}/v2/lat/{latitude}/lon/{longitude}/dist/{radius_nm}")
        response.raise_for_status()
        payload = response.json()
    response_time = int(payload.get("now") or time.time()) if isinstance(payload, dict) else int(time.time())
    aircraft = payload.get("ac", []) if isinstance(payload, dict) else []
    return NearbyResult(records=[record for item in aircraft if isinstance(item, dict) if (record := _record(item, response_time)) is not None], response_time=response_time)
