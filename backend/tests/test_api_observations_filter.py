"""Observation reads: time-range filtering and its validation."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.ingestion.pipeline import ingest_observations
from app.main import create_app
from app.store import get_connection, init_db


def _seed(tmp_path: Path) -> TestClient:
    settings = Settings(database_path=tmp_path / "test.db")
    init_db(settings.database_path)
    with get_connection(settings.database_path) as connection:
        ingest_observations(
            connection,
            [
                {
                    "icao24": "abc123", "observed_at": 100, "latitude": 19.0,
                    "longitude": 72.0, "source": "s", "source_kind": "simulated",
                },
                {
                    "icao24": "abc123", "observed_at": 200, "latitude": 19.1,
                    "longitude": 72.1, "source": "s", "source_kind": "simulated",
                },
                {
                    "icao24": "def456", "observed_at": 300, "latitude": 20.0,
                    "longitude": 73.0, "source": "s", "source_kind": "simulated",
                },
            ],
        )
    return TestClient(create_app(settings))


def test_time_range_filters_by_observed_at(tmp_path: Path) -> None:
    with _seed(tmp_path) as client:
        response = client.get(
            "/api/observations", params={"from_ts": 150, "to_ts": 250}
        )
    assert response.status_code == 200
    rows = response.json()
    assert [row["observed_at"] for row in rows] == [200]


def test_open_ended_ranges_and_reversed_range_rejected(tmp_path: Path) -> None:
    with _seed(tmp_path) as client:
        from_only = client.get("/api/observations", params={"from_ts": 250})
        assert [row["observed_at"] for row in from_only.json()] == [300]
        to_only = client.get("/api/observations", params={"to_ts": 150})
        assert [row["observed_at"] for row in to_only.json()] == [100]
        reversed_range = client.get(
            "/api/observations", params={"from_ts": 300, "to_ts": 100}
        )
        assert reversed_range.status_code == 422
