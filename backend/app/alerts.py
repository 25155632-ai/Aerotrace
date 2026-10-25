"""Alert evaluation, durable outbox payloads, and local WebSocket fan-out.

Alerts are summaries over persisted detector results. They never change the
candidate/verified meaning of events, and their fingerprints are built from
stable detection content rather than transient database row ids.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Iterable

from .store import (
    enqueue_alert_outbox,
    get_connection,
    insert_alert,
    list_alert_rules,
    list_anomalies,
    list_events,
    list_alerts,
)

logger = logging.getLogger("spoofsky.alerts")

SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2}


def _in_region(row: dict, region: dict | None) -> bool:
    if not region:
        return True
    if row.get("latitude") is None or row.get("longitude") is None:
        return False
    return (
        float(region["min_lat"]) <= float(row["latitude"]) <= float(region["max_lat"])
        and float(region["min_lon"]) <= float(row["longitude"]) <= float(region["max_lon"])
    )


def _fingerprint(rule_id: int, rows: Iterable[dict], _event_ids: list[int]) -> str:
    stable = [
        {
            "icao24": row["icao24"],
            "rule": row["rule"],
            "detected_at": row["detected_at"],
            "supporting": sorted(row.get("supporting_observation_ids", [])),
        }
        for row in rows
    ]
    # Event database ids are intentionally excluded: detection runs replace
    # event rows, so those ids are not stable across a repeat analysis.
    raw = json.dumps({"rule": rule_id, "rows": stable}, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _event_members(events: list[dict]) -> dict[int, list[int]]:
    result: dict[int, list[int]] = {}
    for event in events:
        for anomaly_id in event.get("member_anomaly_ids", []):
            result.setdefault(anomaly_id, []).append(int(event["id"]))
    return result


def evaluate_alerts(connection, *, now: int | None = None) -> list[dict]:
    """Evaluate enabled rules against the current persisted detections.

    The detector service calls this only after it has committed its fresh
    results. Duplicate runs therefore produce no new alerts because the
    stable fingerprint is unique.
    """
    if now is None:
        now = int(time.time())
    rules = list_alert_rules(connection)
    anomalies = list_anomalies(connection, limit=10000)
    events = list_events(connection, limit=10000)
    event_members = _event_members(events)
    created: list[dict] = []
    for rule in rules:
        if not rule["enabled"]:
            continue
        eligible = [
            row for row in anomalies
            if SEVERITY_RANK.get(row["severity"], -1) >= SEVERITY_RANK[rule["min_severity"]]
            and (not rule["categories"] or row["kind"] in rule["categories"] or row["rule"] in rule["categories"])
            and (not rule["provenance"] or row["source_kind"] in rule["provenance"])
            and _in_region(row, rule.get("region"))
        ]
        groups: dict[tuple, list[dict]] = {}
        for row in eligible:
            linked_events = event_members.get(int(row["id"]), [])
            # Event-linked anomalies may be consolidated, while unrelated
            # anomalies remain separated by aircraft and detector rule.
            key = ("event", min(linked_events)) if linked_events else ("aircraft-rule", row["icao24"], row["rule"])
            groups.setdefault(key, []).append(row)
        for rows in groups.values():
            rows.sort(key=lambda row: row["detected_at"])
            # Split a long stream into configured time windows, which is a
            # cooldown/grouping boundary rather than a claim that events merge.
            windows: list[list[dict]] = []
            for row in rows:
                if not windows or row["detected_at"] - windows[-1][0]["detected_at"] > rule["group_window_seconds"]:
                    windows.append([row])
                else:
                    windows[-1].append(row)
            for window in windows:
                event_ids = sorted({event_id for row in window for event_id in event_members.get(int(row["id"]), [])})
                fingerprint = _fingerprint(rule["id"], window, event_ids)
                aircraft = sorted({row["icao24"] for row in window})
                source_kinds = sorted({kind for row in window for kind in row.get("source_kinds", [row["source_kind"]])})
                provenance = source_kinds[0] if len(source_kinds) == 1 else "mixed"
                start_at = min(row["detected_at"] for row in window)
                end_at = max(row["detected_at"] for row in window)
                if rule["cooldown_seconds"]:
                    recent = list_alerts(connection, limit=1000)
                    if any(
                        int(alert["created_at"]) <= now
                        and alert["rule_id"] == rule["id"]
                        and alert["category"] == window[0]["kind"]
                        and abs(int(alert["detected_at"]) - start_at) <= rule["cooldown_seconds"]
                        for alert in recent
                    ):
                        continue
                alert_id = insert_alert(
                    connection,
                    rule_id=rule["id"], fingerprint=fingerprint,
                    severity=max(window, key=lambda row: SEVERITY_RANK[row["severity"]])["severity"],
                    category=window[0]["kind"],
                    title=f"{window[0]['kind']} alert",
                    summary=(
                        f"{len(window)} detector finding(s) affecting {len(aircraft)} aircraft "
                        f"between {start_at} and {end_at}. This is an automated alert for investigation, not a verified incident."
                    ),
                    aircraft=aircraft, detected_at=end_at, start_at=start_at, end_at=end_at,
                    latitude=window[0].get("latitude"), longitude=window[0].get("longitude"),
                    provenance=provenance, source_kinds=source_kinds,
                    supporting_event_ids=event_ids,
                    supporting_detection_ids=[int(row["id"]) for row in window],
                    evidence={"rule_name": rule["name"], "detector_rules": sorted({row["rule"] for row in window}), "count": len(window)},
                )
                if alert_id is None:
                    continue
                connection.commit()
                alert = next((item for item in list_alerts(connection, limit=1000) if item["id"] == alert_id), None)
                if alert is None:
                    continue
                payload = {"type": "alert.created", "alert": alert}
                enqueue_alert_outbox(connection, alert_id, payload)
                connection.commit()
                created.append(alert)
    return created


class AlertConnectionManager:
    """Bounded per-client queues for local WebSocket subscribers."""

    def __init__(self, max_queue: int = 100) -> None:
        self.max_queue = max_queue
        self._queues: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    async def connect(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self.max_queue)
        self._loop = asyncio.get_running_loop()
        self._queues.add(queue)
        return queue

    def disconnect(self, queue: asyncio.Queue) -> None:
        self._queues.discard(queue)
        if not self._queues:
            self._loop = None

    def publish_threadsafe(self, payload: dict) -> None:
        if self._loop is None or self._loop.is_closed():
            return
        self._loop.call_soon_threadsafe(asyncio.create_task, self.publish(payload))

    async def publish(self, payload: dict) -> None:
        stale = []
        for queue in tuple(self._queues):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                stale.append(queue)
        for queue in stale:
            self.disconnect(queue)


class AlertDispatcher:
    """Durable local dispatcher. Provider adapters are isolated from API paths."""

    def __init__(self, database_path, settings, manager: AlertConnectionManager) -> None:
        self.database_path = database_path
        self.settings = settings
        self.manager = manager
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None

    async def run(self) -> None:
        while not self._stop.is_set():
            await asyncio.to_thread(self.dispatch_once)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.settings.alert_dispatch_interval_seconds)
            except asyncio.TimeoutError:
                pass

    def dispatch_once(self) -> None:
        # A full provider implementation lives in notifications.py; the local
        # channel is always available and makes the durable event path useful
        # without Redis or third-party credentials.
        from .notifications import deliver_alert
        with get_connection(self.database_path) as connection:
            from .store import claim_outbox, finish_outbox
            for item in claim_outbox(connection, now=int(time.time()), limit=20):
                try:
                    payload = item["payload"]
                    from .broker import publish_optional
                    publish_optional(self.settings.redis_url, payload)
                    self.manager.publish_threadsafe(payload)
                    deliver_alert(connection, payload["alert"], self.settings)
                    finish_outbox(connection, item["id"], success=True)
                except Exception as exc:  # bounded retry, visible in outbox
                    attempts = int(item["attempts"])
                    retry_at = int(time.time()) + min(300, 2 ** min(attempts, 8))
                    finish_outbox(connection, item["id"], success=False, error=type(exc).__name__, retry_at=retry_at if attempts < self.settings.alert_max_attempts else None)
                    logger.warning("alert dispatch failed: %s", type(exc).__name__)
