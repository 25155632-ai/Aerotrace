"""Demo dataset: deterministic counts, labelled provenance, idempotent load."""

from pathlib import Path

from app.ingestion.demo_data import demo_observations
from app.ingestion.pipeline import ingest_observations
from app.store import get_connection, init_db, list_observations


def test_demo_first_and_second_load_counts(tmp_path: Path) -> None:
    path = tmp_path / "test.db"
    init_db(path)
    records = demo_observations()
    assert len(records) == 34
    with get_connection(path) as connection:
        first = ingest_observations(connection, records)
        second = ingest_observations(connection, records)
        rows = list_observations(connection, limit=1000)
    assert (first.total, first.stored, first.duplicates) == (34, 33, 1)
    assert first.missing_coordinates == 1
    assert first.stale == 0
    assert len(first.rejected) == 0
    assert (second.total, second.stored, second.duplicates) == (34, 0, 34)
    assert len(rows) == 33
    assert all(row["source"] == "spoofsky-demo" for row in rows)
    assert all(row["source_kind"] == "simulated" for row in rows)
