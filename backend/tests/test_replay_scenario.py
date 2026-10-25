"""The packaged replay scenario (frontend/public/samples/) must keep
working end to end: it imports cleanly through the real CSV path and
detection on it yields exactly the two events it was designed to
demonstrate — a correlated common-mode event and a position-loss
cluster. The scenario data is synthetic and labelled simulated; it
is a demonstration of the replay mechanism, not a real incident."""

from pathlib import Path

from app.detectors.service import run_detection
from app.ingestion.csv_import import parse_csv_records
from app.ingestion.pipeline import ingest_observations
from app.store import get_connection, init_db

SCENARIO = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "public"
    / "samples"
    / "coordinated-spoof-replay.csv"
)


def test_replay_scenario_imports_and_detects(tmp_path):
    csv_text = SCENARIO.read_text()
    parsed = parse_csv_records(csv_text, "simulated")
    assert parsed.errors == []
    assert len(parsed.valid) == 46
    db_path = tmp_path / "scenario.db"
    init_db(db_path)
    with get_connection(db_path) as connection:
        report = ingest_observations(connection, [r for r, _ in parsed.valid])
        assert report.stored == 46
        assert report.rejected == []
        result = run_detection(connection)
    rules = {d["rule"] for d in result["detections"]}
    assert "common_mode_offset" in rules
    assert "teleport" in rules
    assert "track_gap" in rules
    assert len(result["events"]) == 2
    by_kind = {e["kind"]: e for e in result["events"]}
    correlated = by_kind["correlated_anomalies"]
    assert correlated["aircraft"] == ["c01a01", "c01a02", "c01a03"]
    assert "common_mode_offset" in correlated["member_rules"]
    loss = by_kind["position_loss_cluster"]
    assert loss["aircraft"] == ["6a9001", "6a9002"]
