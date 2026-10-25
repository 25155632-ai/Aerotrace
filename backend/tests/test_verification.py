"""Event verification: human-submitted citations, state transitions,
and history preservation. No detection run may verify an event, and
no verification may happen without a valid citation."""

from fastapi.testclient import TestClient

from app.config import Settings
from app.ingestion.demo_data import demo_observations
from app.ingestion.pipeline import ingest_observations
from app.main import create_app
from app.store import get_connection, init_db


def _client(tmp_path) -> TestClient:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    with get_connection(db_path) as connection:
        ingest_observations(connection, demo_observations())
    app = create_app(Settings(database_path=db_path))
    client = TestClient(app, headers={"X-Requested-With": "XMLHttpRequest"})
    assert client.post("/api/detect").status_code == 200
    return client


def _first_event_id(client: TestClient) -> int:
    events = client.get("/api/events").json()
    assert events, "expected at least one candidate event"
    assert all(e["status"] == "candidate" for e in events)
    return events[0]["id"]


CITATION = {
    "title": "Regional GPS interference report, Example Aviation Authority",
    "publisher": "Example Aviation Authority",
    "reference_url": "https://example.invalid/reports/gps-2026-10",
    "accessed_date": "2026-10-24",
    "note": "Authority bulletin describes interference in the same region and window.",
    "submitted_by": "analyst",
}


def test_verify_marks_event_verified_with_citation(tmp_path):
    client = _client(tmp_path)
    event_id = _first_event_id(client)
    response = client.post(f"/api/events/{event_id}/verify", json=CITATION)
    assert response.status_code == 200
    event = response.json()
    assert event["status"] == "verified"
    verification = event["verification"]
    assert verification["title"] == CITATION["title"]
    assert verification["reference_url"] == CITATION["reference_url"]
    assert verification["accessed_date"] == "2026-10-24"
    assert verification["note"] == CITATION["note"]
    assert verification["submitted_by"] == "analyst"
    assert verification["status"] == "current"
    assert len(event["verification_history"]) == 1
    # The stored list agrees.
    listed = client.get("/api/events").json()
    mine = next(e for e in listed if e["id"] == event_id)
    assert mine["status"] == "verified"
    assert mine["verification"]["title"] == CITATION["title"]


def test_verify_rejects_missing_title_bad_url_and_bad_date(tmp_path):
    client = _client(tmp_path)
    event_id = _first_event_id(client)
    assert client.post(f"/api/events/{event_id}/verify", json={**CITATION, "title": "  "}).status_code == 422
    assert client.post(f"/api/events/{event_id}/verify", json={k: v for k, v in CITATION.items() if k != "title"}).status_code == 422
    assert client.post(f"/api/events/{event_id}/verify", json={**CITATION, "reference_url": "ftp://example.invalid/x"}).status_code == 422
    assert client.post(f"/api/events/{event_id}/verify", json={**CITATION, "accessed_date": "24-10-2026"}).status_code == 422
    assert client.post(f"/api/events/{event_id}/verify", json={k: v for k, v in CITATION.items() if k != "note"}).status_code == 422
    # Nothing was verified by the rejected attempts.
    assert client.get("/api/events").json()[0]["status"] == "candidate"


def test_verify_unknown_event_is_404(tmp_path):
    client = _client(tmp_path)
    assert client.post("/api/events/999999/verify", json=CITATION).status_code == 404


def test_revoke_returns_to_candidate_and_keeps_history(tmp_path):
    client = _client(tmp_path)
    event_id = _first_event_id(client)
    assert client.post(f"/api/events/{event_id}/verify", json=CITATION).status_code == 200
    response = client.post(
        f"/api/events/{event_id}/revoke", json={"reason": "Citation link was wrong."}
    )
    assert response.status_code == 200
    event = response.json()
    assert event["status"] == "candidate"
    assert event["verification"] is None
    assert len(event["verification_history"]) == 1
    assert event["verification_history"][0]["status"] == "revoked"
    assert event["verification_history"][0]["revoked_reason"] == "Citation link was wrong."
    # Revoking a candidate is a conflict, not a silent no-op.
    assert client.post(f"/api/events/{event_id}/revoke", json={}).status_code == 409


def test_reverify_after_revoke_and_supersede_keep_history(tmp_path):
    client = _client(tmp_path)
    event_id = _first_event_id(client)
    client.post(f"/api/events/{event_id}/verify", json=CITATION)
    client.post(f"/api/events/{event_id}/revoke", json={"reason": "wrong link"})
    response = client.post(
        f"/api/events/{event_id}/verify",
        json={**CITATION, "title": "Corrected report title"},
    )
    assert response.json()["status"] == "verified"
    assert len(response.json()["verification_history"]) == 2
    # Supersede: verifying an already-verified event replaces the
    # current record without deleting it.
    response = client.post(
        f"/api/events/{event_id}/verify",
        json={**CITATION, "title": "Second correction"},
    )
    event = response.json()
    assert event["status"] == "verified"
    assert event["verification"]["title"] == "Second correction"
    statuses = [v["status"] for v in event["verification_history"]]
    assert statuses.count("current") == 1
    assert "superseded" in statuses
    assert "revoked" in statuses
    assert len(event["verification_history"]) == 3


def test_detection_after_verification_detaches_but_keeps_records(tmp_path):
    """Regression: re-running detection after verifying an event
    used to 500 with FOREIGN KEY constraint failed, because clearing
    events deleted rows that verification records reference.
    Migration 8 makes the link ON DELETE SET NULL: the run succeeds,
    the record survives detached, and the summary reports it."""
    client = _client(tmp_path)
    event_id = _first_event_id(client)
    response = client.post(f"/api/events/{event_id}/verify", json=CITATION)
    assert response.status_code == 200
    response = client.post("/api/detect")
    assert response.status_code == 200
    assert response.json()["summary"]["detached_verifications"] == 1
    events = client.get("/api/events").json()
    assert all(e["status"] == "candidate" for e in events)
    assert all(e["verification"] is None for e in events)
    db_path = tmp_path / "test.db"
    with get_connection(db_path) as connection:
        row = connection.execute(
            "SELECT event_id, title, status FROM event_verifications"
        ).fetchone()
    assert row["event_id"] is None
    assert row["title"] == CITATION["title"]
    assert row["status"] == "current"


def test_verify_requires_guard_header(tmp_path):
    client = _client(tmp_path)
    event_id = _first_event_id(client)
    from fastapi.testclient import TestClient as TC

    unguarded = TC(client.app)
    response = unguarded.post(f"/api/events/{event_id}/verify", json=CITATION)
    assert response.status_code == 403
