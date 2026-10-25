"""OpenSky adapter: mapping, 429 handling, bounded retries, no silent data."""

import httpx
import pytest

from app.ingestion.opensky import (
    BBox,
    RateLimitedError,
    SourceUnavailableError,
    _build_client,
    fetch_states,
)

STATE = [
    "abc123", "TEST1", "X", 1699999990, 1699999995, 72.87, 19.07, 10000.0,
    False, 250.0, 45.0, 0.0, None, 10050.0, None, False, 0, 0,
]
NO_COORDS = [
    "def456", "TEST2", "X", 1699999990, 1699999995, None, None, None,
    False, None, None, 0.0, None, None, None, False, 0, 0,
]


def _ok_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200, json={"time": 1700000000, "states": [STATE, NO_COORDS, [None]]}
    )


def test_success_maps_state_vector_fields() -> None:
    result = fetch_states(transport=httpx.MockTransport(_ok_response))
    assert result.snapshot_time == 1700000000
    assert result.dropped_without_icao24 == 1
    assert len(result.records) == 2
    record = result.records[0]
    assert record["icao24"] == "abc123"
    assert record["observed_at"] == 1699999990
    assert record["latitude"] == 19.07
    assert record["longitude"] == 72.87
    assert record["baro_altitude_m"] == 10000.0
    assert record["geo_altitude_m"] == 10050.0
    assert record["velocity_ms"] == 250.0
    assert record["heading_deg"] == 45.0
    assert record["callsign"] == "TEST1"
    assert record["source"] == "opensky-live"
    assert record["source_kind"] == "real"
    # Missing coordinates pass through for the pipeline's quality handling.
    assert result.records[1]["latitude"] is None


def test_429_then_success_retries_once() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                429, headers={"X-Rate-Limit-Retry-After-Seconds": "0"}, json={}
            )
        return httpx.Response(200, json={"time": 1700000000, "states": []})

    result = fetch_states(transport=httpx.MockTransport(handler))
    assert calls["n"] == 2
    assert result.records == []


def test_persistent_429_raises_rate_limited_with_retry_after() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            429, headers={"X-Rate-Limit-Retry-After-Seconds": "0"}, json={}
        )

    with pytest.raises(RateLimitedError) as excinfo:
        fetch_states(transport=httpx.MockTransport(handler), max_retries=2)
    assert calls["n"] == 3  # initial attempt + 2 bounded retries
    assert excinfo.value.retry_after_seconds == 0


def test_500_retries_then_unavailable_and_404_does_not_retry() -> None:
    calls = {"n": 0}

    def handler_500(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, json={})

    with pytest.raises(SourceUnavailableError):
        fetch_states(transport=httpx.MockTransport(handler_500), max_retries=1)
    assert calls["n"] == 2

    calls["n"] = 0

    def handler_404(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404, json={})

    with pytest.raises(SourceUnavailableError):
        fetch_states(transport=httpx.MockTransport(handler_404))
    assert calls["n"] == 1


def test_timeout_becomes_unavailable_and_bad_bbox_never_requests() -> None:
    def handler_timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    with pytest.raises(SourceUnavailableError):
        fetch_states(transport=httpx.MockTransport(handler_timeout), max_retries=0)

    def handler_never(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request should have been made")

    with pytest.raises(ValueError):
        fetch_states(
            bbox=BBox(lamin=50.0, lamax=40.0, lomin=0.0, lomax=10.0),
            transport=httpx.MockTransport(handler_never),
        )


def test_bbox_params_are_sent() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"time": 1700000000, "states": []})

    fetch_states(
        bbox=BBox(lamin=10.0, lamax=20.0, lomin=70.0, lomax=80.0),
        transport=httpx.MockTransport(handler),
    )
    assert seen == {"lamin": "10.0", "lamax": "20.0", "lomin": "70.0", "lomax": "80.0"}


def test_unexpected_request_error_becomes_unavailable_not_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A request error outside the retry set (e.g. httpx failing to
    parse proxy configuration) must surface as SourceUnavailableError
    (API: 502, nothing stored), never propagate as a 500."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.InvalidURL("Invalid port: ':1]'")

    with pytest.raises(SourceUnavailableError):
        fetch_states(transport=httpx.MockTransport(handler))


def test_production_client_ignores_hostile_proxy_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plain httpx.Client() construction raises InvalidURL in
    environments whose NO_PROXY lists bare or bracketed IPv6
    addresses. The production client must construct regardless,
    reading only the proxy URL itself."""
    monkeypatch.setenv(
        "NO_PROXY",
        "localhost,127.0.0.1,::1,[::1],fd8b:4f84:7d32:99::1,"
        "[fd8b:4f84:7d32:99::1]",
    )
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    client = _build_client(timeout_seconds=5.0, transport=None)
    assert client.timeout.connect == 5.0
    client.close()
