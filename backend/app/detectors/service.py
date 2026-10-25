"""Detection service: stored observations -> persisted results.

Pipeline (each step is a separate module, per the stage requirements):
validation (ingestion) -> feature extraction (features.py) ->
detection (detectors.py) -> clustering (clustering.py) ->
interpretation (clustering.py) -> persistence (store.py).

A run replaces previous detection results. That is deliberate: the
stored anomalies/events always describe the *current* stored
observations, so a re-run after new ingestion cannot leave stale
alerts mixed with fresh ones. The raw observations are never deleted;
only derived results are replaced. Human verification records are
never deleted either: replacing events detaches their records
(migration 8), and the run summary counts how many were detached,
so the UI can report that previous verifications no longer attach
to any current event.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections import Counter

from ..store import (
    clear_detection_results,
    count_attached_verifications,
    insert_anomaly,
    insert_event,
    link_event_anomaly,
    list_observations,
)
from ..alerts import evaluate_alerts
from .clustering import (
    clusters_for_interpretation,
    interpret_clusters,
    interpret_quality_clusters,
)
from .detectors import DetectorConfig, run_all_detectors

# The backend previously logged nothing at all: a detection run that
# replaced all stored results left no trace. Runs now log their scope
# and counts (never observation contents or identifiers beyond the
# filter values, which are already API parameters).
logger = logging.getLogger("spoofsky.detection")


def run_detection(
    connection: sqlite3.Connection,
    *,
    source_kind: str | None = None,
    icao24: str | None = None,
    config: DetectorConfig | None = None,
    cluster_time_window_s: int = 600,
    cluster_max_distance_km: float = 100.0,
) -> dict:
    config = config or DetectorConfig()
    observations = list_observations(
        connection,
        icao24=icao24,
        source_kind=source_kind,
        limit=10000,
    )
    detections = run_all_detectors(observations, config)

    # DBSCAN is the clustering algorithm (see clustering.py).
    # clusters_for_interpretation wraps noise as singleton clusters
    # so interpretation keeps its rule: a lone high-severity
    # detection still becomes a single-aircraft event, while lone
    # medium/low detections stay un-evented.
    anomaly_clusters = clusters_for_interpretation(
        [d for d in detections if d.kind == "anomaly"],
        eps_time_s=cluster_time_window_s,
        eps_km=cluster_max_distance_km,
    )
    quality_clusters = clusters_for_interpretation(
        [d for d in detections if d.kind == "data_quality"],
        eps_time_s=cluster_time_window_s,
        eps_km=cluster_max_distance_km,
    )
    events = interpret_clusters(
        anomaly_clusters,
        time_window_s=cluster_time_window_s,
        max_distance_km=cluster_max_distance_km,
    ) + interpret_quality_clusters(quality_clusters)

    # Persist, replacing previous results (see module docstring).
    # Explicit commit: callers may hold the connection without a
    # transaction context, and uncommitted inserts would be lost.
    # Verification records attached to the replaced events are
    # detached, not deleted (migration 8): deleting events nulls
    # their rows via ON DELETE SET NULL, and the count is reported
    # so the UI can say the previous verifications no longer attach
    # to any current event.
    detached_verifications = count_attached_verifications(connection)
    clear_detection_results(connection)
    detection_ids: dict[int, int] = {}
    stored_detections: list[dict] = []
    for detection in detections:
        row_id = insert_anomaly(
            connection,
            icao24=detection.icao24,
            detected_at=detection.detected_at,
            rule=detection.rule,
            severity=detection.severity,
            kind=detection.kind,
            latitude=detection.latitude,
            longitude=detection.longitude,
            evidence=detection.evidence,
            source_kind=detection.source_kind,
            supporting_observation_ids=detection.supporting_observation_ids,
            source_kinds=detection.source_kinds,
        )
        detection_ids[id(detection)] = row_id
        item = detection.to_dict()
        item["id"] = row_id
        stored_detections.append(item)
    stored_events: list[dict] = []
    for event in events:
        event_id = insert_event(
            connection,
            kind=event.kind,
            severity=event.severity,
            start_at=event.start_at,
            end_at=event.end_at,
            centroid_lat=event.centroid_lat,
            centroid_lon=event.centroid_lon,
            aircraft=event.aircraft,
            summary=event.summary,
            created_at=int(time.time()),
        )
        for member in event.members:
            member_id = detection_ids.get(id(member))
            if member_id is not None:
                link_event_anomaly(connection, event_id, member_id)
        item = event.to_dict()
        item["id"] = event_id
        item["status"] = "candidate"
        item["member_anomaly_ids"] = [
            detection_ids[id(m)] for m in event.members if id(m) in detection_ids
        ]
        stored_events.append(item)
    connection.commit()
    created_alerts = evaluate_alerts(connection)
    connection.commit()
    logger.info(
        "detection run: source_kind=%s icao24=%s observations=%d "
        "detections=%d events=%d detached_verifications=%d",
        source_kind,
        icao24,
        len(observations),
        len(detections),
        len(events),
        detached_verifications,
    )

    return {
        "observations_analysed": len(observations),
        "aircraft_analysed": len({o["icao24"] for o in observations}),
        "detections": stored_detections,
        "events": stored_events,
        "summary": {
            "detections_by_rule": dict(Counter(d.rule for d in detections)),
            "detections_by_severity": dict(Counter(d.severity for d in detections)),
            "events_by_kind": dict(Counter(e.kind for e in events)),
            "detections_total": len(detections),
            "events_total": len(events),
            "detached_verifications": detached_verifications,
            "alerts_created": len(created_alerts),
        },
    }
