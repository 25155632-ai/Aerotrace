"""OpenSky Network live state-vector adapter (read-only).

Access method verified against the OpenSky REST API documentation
(openskynetwork.github.io/opensky-api/rest.html) before this adapter
was written: `GET https://opensky-network.org/api/states/all`, anonymous
access permitted, optional bounding-box filters `lamin`/`lamax`/`lomin`/
`lomax` and `icao24` filters, credit-based rate limits, 429 responses
with an `X-Rate-Limit-Retry-After-Seconds` header. No other OpenSky
endpoint is used here, and no historical (Trino) access is assumed.

Behaviour on failure is the point of this module:
- Timeouts, connection errors, and 5xx responses: bounded retries
  (default 2 retries, 1s then 2s), then SourceUnavailableError.
- 429 rate limiting: the server's retry-after value is honoured when
  present and sane (<= 60s); otherwise a 2s/4s backoff. After bounded
  retries, RateLimitedError — the caller must surface "live source
  rate-limited", not data.
- 4xx other than 429: no retry, SourceUnavailableError.
- This adapter NEVER falls back to demo or simulated data. A failed
  live fetch produces an error, and the API layer turns that into a
  502 with no observations stored. Silent substitution is forbidden
  by the project rules.

State-vector array layout (from the documented response format):
index 0 icao24, 1 callsign, 3 time_position, 4 last_contact,
5 longitude, 6 latitude, 7 barometric altitude (m),
9 velocity (m/s), 10 true track (deg), 13 geometric altitude (m).
Vectors without an icao24 cannot be attributed to an aircraft and are
dropped by the adapter (counted in the result, not silently hidden:
see FetchResult.dropped_without_icao24). Vectors with missing
coordinates are passed through; the pipeline records them as
data-quality records.
"""

from __future__ import annotations

import os
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from ..config import OPENSKY_BASE_URL

DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_RETRIES = 2
MAX_RETRY_AFTER_SECONDS = 60


class SourceError(Exception):
    """Base class for live-source failures. Never carries credentials."""


class SourceUnavailableError(SourceError):
    """The live source could not be reached or returned an error."""


class RateLimitedError(SourceError):
    """The live source rate-limited the request."""

    def __init__(self, message: str, retry_after_seconds: int | None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


@dataclass
class BBox:
    lamin: float
    lamax: float
    lomin: float
    lomax: float

    def validate(self) -> None:
        if not (-90.0 <= self.lamin <= 90.0 and -90.0 <= self.lamax <= 90.0):
            raise ValueError("bbox latitudes must be in [-90, 90]")
        if not (-180.0 <= self.lomin <= 180.0 and -180.0 <= self.lomax <= 180.0):
            raise ValueError("bbox longitudes must be in [-180, 180]")
        if self.lamin >= self.lamax or self.lomin >= self.lomax:
            raise ValueError("bbox minimum must be below maximum")

    def params(self) -> dict[str, float]:
        return {
            "lamin": self.lamin,
            "lamax": self.lamax,
            "lomin": self.lomin,
            "lomax": self.lomax,
        }


@dataclass
class FetchResult:
    records: list[dict]
    dropped_without_icao24: int
    snapshot_time: int | None


def _parse_retry_after(response: httpx.Response) -> int | None:
    raw = response.headers.get("X-Rate-Limit-Retry-After-Seconds")
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if 0 <= value <= MAX_RETRY_AFTER_SECONDS else None


def _state_to_record(state: list, snapshot_time: int, received_at: int) -> dict | None:
    icao24 = state[0] if len(state) > 0 else None
    if not icao24:
        return None
    time_position = state[3] if len(state) > 3 else None
    observed_at = time_position if time_position is not None else snapshot_time
    callsign = state[1] if len(state) > 1 else None
    return {
        "icao24": str(icao24).strip().lower(),
        "observed_at": observed_at,
        "latitude": state[6] if len(state) > 6 else None,
        "longitude": state[5] if len(state) > 5 else None,
        "baro_altitude_m": state[7] if len(state) > 7 else None,
        "geo_altitude_m": state[13] if len(state) > 13 else None,
        "velocity_ms": state[9] if len(state) > 9 else None,
        "heading_deg": state[10] if len(state) > 10 else None,
        "callsign": str(callsign).strip() if callsign else None,
        "source": "opensky-live",
        "source_kind": "real",
        "received_at": received_at,
    }


def _build_client(
    *, timeout_seconds: float, transport: httpx.BaseTransport | None
) -> httpx.Client:
    """Build the HTTP client for the one upstream this app contacts.

    With an explicit test transport, httpx is used as-is. For a real
    connection the client must NOT inherit the process proxy
    environment wholesale: httpx parses NO_PROXY entries as URL
    patterns, and environments whose NO_PROXY lists bare or bracketed
    IPv6 addresses (observed in this runtime) make plain
    ``httpx.Client()`` construction itself raise InvalidURL — before
    any request, and outside the retry handling. The app talks to
    exactly one public host, so it reads the configured proxy URL
    explicitly (HTTPS_PROXY, else HTTP_PROXY) and disables environment
    inheritance; with no proxy configured it connects directly. The
    proxy URL is never logged anywhere in this module.
    """
    if transport is not None:
        return httpx.Client(timeout=timeout_seconds, transport=transport)
    proxy = (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
        or None
    )
    # verify: the platform default SSL context, not httpx's bundled
    # certifi store. Managed environments (egress proxies with their
    # own CA, corporate CAs) put their roots in the platform store;
    # the bundled store rejects their intercepted chains with
    # CERTIFICATE_VERIFY_FAILED even though the proxy is legitimate.
    # Verification stays ON — this is not verify=False.
    return httpx.Client(
        timeout=timeout_seconds,
        trust_env=False,
        proxy=proxy,
        verify=ssl.create_default_context(),
    )


def fetch_states(
    *,
    bbox: BBox | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    transport: httpx.BaseTransport | None = None,
    now_fn: Callable[[], int] | None = None,
) -> FetchResult:
    """Fetch one OpenSky snapshot. Raises SourceError on failure.

    `transport` exists for tests (httpx MockTransport); production
    passes None and uses a real connection. `now_fn`, when given, is a
    callable returning epoch seconds, used as received_at for testing.
    """
    if bbox is not None:
        bbox.validate()
    params = bbox.params() if bbox is not None else {}

    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            with _build_client(
                timeout_seconds=timeout_seconds, transport=transport
            ) as client:
                response = client.get(
                    f"{OPENSKY_BASE_URL}/api/states/all",
                    params=params,
                    headers={"User-Agent": "Aerotrace/0.1.0 (research prototype)"},
                )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = exc
            if attempt < max_retries:
                time.sleep(1.0 * (attempt + 1))
                continue
            raise SourceUnavailableError(
                f"OpenSky request failed: {type(exc).__name__}"
            ) from exc
        except Exception as exc:
            # Anything else from client construction or the request
            # (for example httpx failing to parse the environment's
            # proxy configuration) is still a source failure, never a
            # 500: the API contract is "error, nothing stored". No
            # retry — a configuration-shaped error will not heal by
            # retrying — and no detail that could carry credentials.
            raise SourceUnavailableError(
                f"OpenSky request failed: {type(exc).__name__}"
            ) from exc

        if response.status_code == 200:
            try:
                payload = response.json()
            except ValueError as exc:
                raise SourceUnavailableError(
                    "OpenSky returned a non-JSON response"
                ) from exc
            if not isinstance(payload, dict):
                raise SourceUnavailableError("OpenSky returned an unexpected response")
            snapshot_time = payload.get("time")
            states = payload.get("states") or []
            received_at = int(now_fn()) if now_fn is not None else int(time.time())
            records: list[dict] = []
            dropped = 0
            for state in states:
                record = _state_to_record(state, snapshot_time, received_at)
                if record is None:
                    dropped += 1
                else:
                    records.append(record)
            return FetchResult(
                records=records,
                dropped_without_icao24=dropped,
                snapshot_time=snapshot_time if isinstance(snapshot_time, int) else None,
            )

        if response.status_code == 429:
            retry_after = _parse_retry_after(response)
            if attempt < max_retries:
                time.sleep(float(retry_after) if retry_after is not None else 2.0 * (attempt + 1))
                continue
            raise RateLimitedError(
                "OpenSky rate limit reached (HTTP 429)",
                retry_after_seconds=retry_after,
            )

        if 500 <= response.status_code < 600 and attempt < max_retries:
            time.sleep(1.0 * (attempt + 1))
            continue
        raise SourceUnavailableError(
            f"OpenSky returned HTTP {response.status_code}"
        )

    # Unreachable in practice: the loop always raises or returns.
    raise SourceUnavailableError(f"OpenSky request failed: {last_error}")
