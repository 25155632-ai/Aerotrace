"""Security audit regression tests (Stage 6).

Each test probes a real attack surface of this architecture, not a
checklist item: parameterised SQL (injection strings must return
nothing, never a dump), JSON content types (no HTML sink for stored
strings), strict query validation, the same-origin guard for POST,
CORS allowlisting, SSRF-proof upstream handling, and the loopback
access model. Where a risk does not apply, the test documents why
(e.g. no sessions exist to hijack, so there is no session test).
"""

import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.ingestion.demo_data import demo_observations
from app.ingestion.observations import ObservationError, normalize_observation
from app.ingestion.pipeline import ingest_observations
from app.main import create_app
from app.store import get_connection, init_db

GUARD = {"X-Requested-With": "XMLHttpRequest"}


def _client(tmp_path, *, guarded: bool = True) -> TestClient:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    with get_connection(db_path) as connection:
        ingest_observations(connection, demo_observations())
    app = create_app(Settings(database_path=db_path))
    headers = GUARD if guarded else {}
    return TestClient(app, headers=headers)


def test_sql_injection_strings_return_nothing_not_a_dump(tmp_path):
    client = _client(tmp_path)
    # Classic tautology and union attempts in every string filter.
    for params in (
        {"icao24": "' OR '1'='1"},
        {"icao24": "abc123' OR 1=1 --"},
        {"rule": "teleport' OR '1'='1"},
        {"severity": "high' OR '1'='1"},
    ):
        response = client.get("/api/anomalies", params=params)
        assert response.status_code == 200
        assert response.json() == []
    # The same probe against observations: no rows leak.
    response = client.get(
        "/api/observations", params={"icao24": "' OR '1'='1"}
    )
    assert response.json() == []
    # And a non-integer ids value is rejected by validation, not
    # interpolated into SQL.
    response = client.get("/api/observations", params={"ids": "1; DROP TABLE observations"})
    assert response.status_code == 422
    # The table survived (a DROP would have made this fail).
    assert client.get("/api/observations").status_code == 200


def test_stored_strings_are_json_not_html_and_callsign_charset_is_restricted(tmp_path):
    client = _client(tmp_path)
    # A stored callsign with markup must never reach a browser as HTML:
    # the API returns JSON, and React renders it as text. The probe
    # here is the content type plus the charset rule at ingestion.
    response = client.get("/api/observations", params={"icao24": "abc123"})
    assert "application/json" in response.headers["content-type"]
    # Ingestion rejects markup callsigns outright (defence in depth
    # for the Leaflet tooltip, which renders strings as HTML).
    with pytest.raises(ObservationError):
        normalize_observation({
            "icao24": "abc123",
            "observed_at": 1700000000,
            "latitude": 19.0,
            "longitude": 72.0,
            "callsign": "<script>",
            "source": "test",
            "source_kind": "simulated",
        })
    # Ordinary callsigns still pass.
    record = normalize_observation({
        "icao24": "abc123",
        "observed_at": 1700000000,
        "latitude": 19.0,
        "longitude": 72.0,
        "callsign": "TEST1",
        "source": "test",
        "source_kind": "simulated",
    })
    assert record.callsign == "TEST1"


def test_query_validation_rejects_bad_sort_limit_and_ranges(tmp_path):
    client = _client(tmp_path)
    assert client.get("/api/anomalies", params={"sort": "detected_at; DROP"}).status_code == 422
    assert client.get("/api/anomalies", params={"limit": 0}).status_code == 422
    assert client.get("/api/anomalies", params={"limit": 1001}).status_code == 422
    assert client.get("/api/anomalies", params={"offset": -1}).status_code == 422
    assert client.get("/api/anomalies", params={"min_lat": 100}).status_code == 422
    assert client.get("/api/anomalies", params={"from_ts": -1}).status_code == 422
    # Uppercase aircraft codes are normalised, not rejected or missed.
    lower = client.get("/api/observations", params={"icao24": "abc123"}).json()
    upper = client.get("/api/observations", params={"icao24": "ABC123"}).json()
    assert lower and lower == upper


def test_post_without_same_origin_header_is_rejected(tmp_path):
    # A client with no header models a plain cross-origin form POST,
    # which is the attack the guard exists to stop. It is not
    # authentication: the header proves origin handling, not identity.
    unguarded = _client(tmp_path, guarded=False)
    assert unguarded.post("/api/detect").status_code == 403
    assert unguarded.post("/api/ingest/demo").status_code == 403
    # With the header, the same calls succeed.
    guarded = _client(tmp_path, guarded=True)
    assert guarded.post("/api/detect").status_code == 200
    # GETs are unaffected by the guard either way.
    assert unguarded.get("/api/observations").status_code == 200


def test_oversized_post_body_is_rejected(tmp_path):
    client = _client(tmp_path)
    response = client.post(
        "/api/ingest/demo",
        content=b"x" * 1_000_001,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 413


def test_cors_allows_only_the_local_frontend_origin(tmp_path):
    client = _client(tmp_path)
    allowed = client.options(
        "/api/observations",
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert allowed.headers.get("access-control-allow-origin") == "http://127.0.0.1:5173"
    foreign = client.options(
        "/api/observations",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "access-control-allow-origin" not in foreign.headers
    # No wildcard is ever emitted.
    assert allowed.headers.get("access-control-allow-origin") != "*"


def test_opensky_upstream_is_fixed_not_user_controlled(tmp_path):
    # The adapter's host is a constant; a bbox can only add validated
    # query parameters, never redirect the request. The mock transport
    # records the URL actually requested.
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, json={"time": 1700000000, "states": []})

    app = create_app(Settings(database_path=tmp_path / "ssrf.db"))
    app.state.opensky_transport = httpx.MockTransport(handler)
    client = TestClient(app, headers=GUARD)
    response = client.post(
        "/api/ingest/opensky",
        json={"lamin": 10.0, "lamax": 20.0, "lomin": 70.0, "lomax": 80.0},
    )
    assert response.status_code == 200
    assert requested == [
        "https://opensky-network.org/api/states/all?lamin=10.0&lamax=20.0&lomin=70.0&lomax=80.0"
    ]
    # An out-of-range bbox is rejected before any request is made.
    requested.clear()
    assert (
        client.post(
            "/api/ingest/opensky",
            json={"lamin": -100.0, "lamax": 20.0, "lomin": 70.0, "lomax": 80.0},
        ).status_code
        == 422
    )
    assert requested == []


def test_loopback_access_model_and_error_shapes(tmp_path):
    # The access model is loopback-only with no accounts: defaults must
    # say so, and error responses must not leak internals.
    settings = Settings.from_env()
    assert settings.host == "127.0.0.1"
    assert settings.frontend_origin == "http://127.0.0.1:5173"
    client = _client(tmp_path)
    unknown = client.get("/api/definitely-not-a-route")
    assert unknown.status_code == 404
    assert "Traceback" not in unknown.text
    assert "spoofsky.db" not in unknown.text
    bad = client.get("/api/observations", params={"source_kind": "bogus"})
    assert bad.status_code == 422
    assert "Traceback" not in bad.text


def test_detection_run_is_logged(tmp_path, caplog):
    client = _client(tmp_path)
    with caplog.at_level(logging.INFO, logger="spoofsky.detection"):
        response = client.post("/api/detect")
    assert response.status_code == 200
    assert any("detection run:" in record.message for record in caplog.records)
    # 19, not the historical 16: the demo's three aircraft jump by
    # the same ~62 km vector at the same time, so the common-mode
    # detector adds 3 detections (one per aircraft). The count pins
    # the detector set, not a magic number.
    assert any("detections=19" in record.message for record in caplog.records)


def test_internal_error_response_hides_details(tmp_path):
    """A genuine internal failure (an unusable database path) must
    surface as a plain 500 with no traceback, filesystem path, or
    diagnostic detail — pinned here after the audit verified it only
    with a throwaway probe."""
    app = create_app(Settings(database_path=tmp_path))
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/api/observations")
    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    assert "Traceback" not in response.text
    assert str(tmp_path) not in response.text
