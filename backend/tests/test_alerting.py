from pathlib import Path

from app.detectors.service import run_detection
from app.ingestion.demo_data import demo_observations
from app.ingestion.pipeline import ingest_observations
from app.store import get_connection, init_db, list_alerts, list_alert_rules


def test_detection_creates_durable_alerts_and_duplicate_run_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "alerts.db"
    init_db(path)
    with get_connection(path) as connection:
        ingest_observations(connection, demo_observations())
        first = run_detection(connection)
        first_alerts = list_alerts(connection)
        run_detection(connection)
        second_alerts = list_alerts(connection)
        assert list_alert_rules(connection)
    assert first["summary"]["alerts_created"] > 0
    assert len(second_alerts) == len(first_alerts)
    assert all(alert["provenance"] in {"real", "simulated", "mixed"} for alert in first_alerts)
    assert all(alert["supporting_detection_ids"] for alert in first_alerts)
