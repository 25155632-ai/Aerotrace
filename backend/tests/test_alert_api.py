from fastapi.testclient import TestClient

from app.config import Settings
from app.ingestion.demo_data import demo_observations
from app.ingestion.pipeline import ingest_observations
from app.main import create_app
from app.store import get_connection, init_db


def test_alert_api_and_websocket_preserve_origin_boundary(tmp_path):
    db_path = tmp_path / "alert-api.db"
    init_db(db_path)
    with get_connection(db_path) as connection:
        ingest_observations(connection, demo_observations())
    settings = Settings(database_path=db_path)
    app = create_app(settings)
    with TestClient(app, headers={"X-Requested-With": "XMLHttpRequest"}) as client:
        assert client.get("/api/ml/status").status_code == 200
        assert client.get("/api/alerting/health").status_code == 200
        response = client.post("/api/detect")
        assert response.status_code == 200
        alerts = client.get("/api/alerts").json()
        assert alerts
        assert alerts[0]["provenance"] == "simulated"
        with client.websocket_connect("/api/ws/alerts", headers={"origin": settings.frontend_origin}) as websocket:
            assert websocket.receive_json()["type"] == "connection.status"
