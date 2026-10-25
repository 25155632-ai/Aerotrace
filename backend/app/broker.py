"""Optional Redis distribution adapter; SQLite outbox remains authoritative."""

from __future__ import annotations

import json


def publish_optional(redis_url: str | None, payload: dict) -> str:
    if not redis_url:
        return "local"
    try:
        import redis
    except ImportError:
        return "redis_unavailable"
    try:
        client = redis.Redis.from_url(redis_url, socket_timeout=2, socket_connect_timeout=2)
        client.publish("spoofsky.alerts", json.dumps(payload, sort_keys=True))
        return "redis"
    except Exception:
        # A broker outage must not break local in-app delivery or ingestion.
        return "redis_failed"
