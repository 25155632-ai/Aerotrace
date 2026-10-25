"""CSV import: preview stores nothing, commit stores valid rows via
the standard pipeline, failures carry CSV line numbers, and uploaded
rows keep their 'csv-import' provenance."""

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.store import init_db

HEADER = "icao24,observed_at,latitude,longitude,baro_altitude_m,geo_altitude_m,velocity_ms,heading_deg,callsign\n"
GOOD = (
    "abcdef,1700000000,19.0,72.0,10000,10000,250,45,TEST1\n"
    "abcdef,1700000010,19.01,72.01,10000,10000,250,45,TEST1\n"
)


def _client(tmp_path) -> TestClient:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    app = create_app(Settings(database_path=db_path))
    return TestClient(app, headers={"X-Requested-With": "XMLHttpRequest"})


def _observations(client: TestClient) -> list[dict]:
    return client.get("/api/observations").json()


def test_preview_validates_without_storing(tmp_path):
    client = _client(tmp_path)
    response = client.post(
        "/api/ingest/csv/preview",
        json={"csv_text": HEADER + GOOD, "source_kind": "simulated"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total_rows"] == 2
    assert body["valid_rows"] == 2
    assert body["error_rows"] == 0
    assert body["duplicate_rows"] == 0
    assert _observations(client) == []


def test_commit_stores_rows_with_csv_import_provenance(tmp_path):
    client = _client(tmp_path)
    response = client.post(
        "/api/ingest/csv", json={"csv_text": HEADER + GOOD, "source_kind": "simulated"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["stored"] == 2
    assert body["duplicates"] == 0
    rows = _observations(client)
    assert len(rows) == 2
    assert all(r["source"] == "csv-import" for r in rows)
    assert all(r["source_kind"] == "simulated" for r in rows)
    assert rows[0]["callsign"] == "TEST1"


def test_malformed_rows_report_line_numbers_and_valid_rows_commit(tmp_path):
    client = _client(tmp_path)
    text = (
        HEADER
        + "abcdef,1700000000,19.0,72.0,10000,10000,250,45,TEST1\n"
        + "abcdef,not-a-time,19.0,72.0,10000,10000,250,45,TEST1\n"
        + "abcdef,1700000020,200.0,72.0,10000,10000,250,45,TEST1\n"
        + "xyz,1700000030,19.0,72.0,10000,10000,250,45,TEST1\n"
    )
    response = client.post("/api/ingest/csv", json={"csv_text": text})
    assert response.status_code == 200
    body = response.json()
    assert body["stored"] == 1
    assert body["error_rows"] == 3
    assert [e["line"] for e in body["errors"]] == [3, 4, 5]


def test_duplicate_policy_matches_ingestion(tmp_path):
    client = _client(tmp_path)
    body = {"csv_text": HEADER + GOOD}
    assert client.post("/api/ingest/csv", json=body).json()["stored"] == 2
    second = client.post("/api/ingest/csv", json=body).json()
    assert second["stored"] == 0
    assert second["duplicates"] == 2
    # A conflicting position at an already-seen timestamp is stored,
    # not dropped (position-aware duplicate key).
    conflict = HEADER + "abcdef,1700000000,21.0,74.0,10000,10000,250,45,TEST1\n"
    third = client.post("/api/ingest/csv", json={"csv_text": conflict}).json()
    assert third["stored"] == 1
    assert third["duplicates"] == 0


def test_preview_flags_duplicates_from_prior_import(tmp_path):
    client = _client(tmp_path)
    client.post("/api/ingest/csv", json={"csv_text": HEADER + GOOD})
    preview = client.post(
        "/api/ingest/csv/preview", json={"csv_text": HEADER + GOOD}
    ).json()
    assert preview["duplicate_rows"] == 2
    assert preview["valid_rows"] == 2


def test_real_kind_is_stored_only_when_asserted(tmp_path):
    client = _client(tmp_path)
    client.post(
        "/api/ingest/csv",
        json={"csv_text": HEADER + GOOD, "source_kind": "real"},
    )
    rows = _observations(client)
    assert all(r["source_kind"] == "real" for r in rows)
    assert all(r["source"] == "csv-import" for r in rows)


def test_file_level_errors_are_422(tmp_path):
    client = _client(tmp_path)
    assert client.post("/api/ingest/csv", json={"csv_text": ""}).status_code == 422
    assert client.post(
        "/api/ingest/csv", json={"csv_text": "latitude,longitude\n19.0,72.0\n"}
    ).status_code == 422
    assert client.post(
        "/api/ingest/csv", json={"csv_text": "icao24,observed_at\n"}
    ).status_code == 200  # Header only: zero rows, not an error.
    rows = "icao24,observed_at\n" + "".join(
        f"abcdef,{1700000000 + i * 10}\n" for i in range(10_001)
    )
    assert client.post("/api/ingest/csv", json={"csv_text": rows}).status_code == 422


def test_unknown_columns_ignored_and_iso_timestamps_accepted(tmp_path):
    client = _client(tmp_path)
    text = (
        "icao24,observed_at,latitude,longitude,flight_number,extra\n"
        "abcdef,2023-11-14T22:13:20+00:00,19.0,72.0,BA123,ignored\n"
    )
    response = client.post("/api/ingest/csv", json={"csv_text": text})
    assert response.status_code == 200
    assert response.json()["stored"] == 1
    assert _observations(client)[0]["observed_at"] == 1700000000


def test_formula_like_cells_are_rejected_not_stored(tmp_path):
    client = _client(tmp_path)
    text = HEADER + "abcdef,1700000000,=2+2,72.0,10000,10000,250,45,TEST1\n"
    response = client.post("/api/ingest/csv", json={"csv_text": text})
    assert response.json()["stored"] == 0
    assert response.json()["error_rows"] == 1
    assert _observations(client) == []


def test_preview_returns_normalized_recording_without_writing(tmp_path):
    client = _client(tmp_path)
    response = client.post(
        "/api/ingest/csv/preview",
        json={"csv_text": HEADER + GOOD + "bad,1700000000,999,0,,,,,\n", "source_kind": "real"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["error_rows"] == 1
    rows = body["observations"]
    assert len(rows) == 2
    assert [r["observed_at"] for r in rows] == [1700000000, 1700000010]
    assert all(r["source"] == "csv-import" and r["source_kind"] == "real" for r in rows)
    assert rows[0]["latitude"] == 19.0
    assert rows[0]["id"] < 0
    assert _observations(client) == []
