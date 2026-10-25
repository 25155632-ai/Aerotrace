"""Health endpoint: the only route in the shell stage."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    settings = Settings(database_path=tmp_path / "test.db")
    return TestClient(create_app(settings))


def test_health_returns_ok(client: TestClient) -> None:
    with client:
        response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == "0.1.0"
    assert body["database"] == "ready"


def test_root_redirects_to_frontend(client: TestClient) -> None:
    with client:
        response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "http://127.0.0.1:5173"


def test_unknown_route_is_plain_404(client: TestClient) -> None:
    # No stack trace, no traceback text, no internal detail leaks.
    with client:
        response = client.get("/api/tracks")
    assert response.status_code == 404
    assert "traceback" not in response.text.lower()
