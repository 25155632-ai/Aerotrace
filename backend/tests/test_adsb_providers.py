import httpx

from app.ingestion.adsb_lol import fetch_nearby


def test_adsb_lol_adapter_normalizes_provider_fields():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "/v2/lat/12.0/lon/77.0/dist/20" in str(request.url)
        return httpx.Response(200, json={"now": 1700000100, "ac": [{"hex": "abc123", "lat": 12.1, "lon": 77.1, "alt_baro": 1000, "gs": 100, "track": 90, "flight": " TEST "}]})

    result = fetch_nearby(12.0, 77.0, 20, transport=httpx.MockTransport(handler))
    assert len(result.records) == 1
    assert result.records[0]["source"] == "adsb.lol"
    assert result.records[0]["source_kind"] == "real"
    assert result.records[0]["callsign"] == "TEST"
