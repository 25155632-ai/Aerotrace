"""Optional airplanes.live v2 nearby-aircraft adapter."""

from __future__ import annotations

from .adsb_lol import NearbyResult, _record
from ..config import AIRPLANES_LIVE_BASE_URL
import httpx
import time


def fetch_nearby(latitude: float, longitude: float, radius_nm: int, *, timeout_seconds: float = 10.0, transport=None) -> NearbyResult:
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180 and 1 <= radius_nm <= 250):
        raise ValueError("invalid airplanes.live nearby query")
    kwargs = {"timeout": timeout_seconds}
    if transport is not None:
        kwargs["transport"] = transport
    with httpx.Client(**kwargs) as client:
        response = client.get(f"{AIRPLANES_LIVE_BASE_URL}/v2/lat/{latitude}/lon/{longitude}/dist/{radius_nm}")
        response.raise_for_status()
        payload = response.json()
    response_time = int(payload.get("now") or time.time()) if isinstance(payload, dict) else int(time.time())
    aircraft = payload.get("ac", []) if isinstance(payload, dict) else []
    records = []
    for item in aircraft:
        if isinstance(item, dict):
            record = _record(item, response_time)
            if record is not None:
                record["source"] = "airplanes.live"
                records.append(record)
    return NearbyResult(records=records, response_time=response_time)
