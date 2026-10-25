"""Bounded in-memory cache for read endpoints.

Why this exists: the explorer, investigation, and auto-refresh paths
re-read the same list queries repeatedly, and at demo scale the
database answers in under a millisecond — so this cache is not a
performance fix, it is a bound on repeated identical reads. The rules
that keep it honest:

- Short TTL (15 seconds): a cached answer is never far behind the
  database, and the UI's own "fetched at" timestamp still describes
  when the frontend last asked, not when the cache last refreshed.
- Write invalidation: every mutating API route (ingest, detect,
  verify, revoke, CSV import) clears the cache, so a new observation
  or a changed event status is visible on the very next read — the
  cache can never hide a just-completed write.
- Bounded size (200 entries, oldest evicted first) and per-process
  scope: this is a single-process loopback prototype, so an in-memory
  cache cannot go stale across processes — there is only one.
- Only list reads are cached (observations, anomalies, events).
  Sources status is never cached: it must reflect the latest fetch
  log row, and fetch attempts are comparatively rare.
"""

from __future__ import annotations

import time
from collections import OrderedDict

TTL_SECONDS = 15
MAX_ENTRIES = 200


class ReadCache:
    def __init__(self, ttl_seconds: int = TTL_SECONDS, max_entries: int = MAX_ENTRIES):
        self._ttl = ttl_seconds
        self._max = max_entries
        self._entries: OrderedDict[str, tuple[float, object]] = OrderedDict()

    def get(self, key: str) -> object | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() >= expires_at:
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return value

    def put(self, key: str, value: object) -> None:
        self._entries[key] = (time.monotonic() + self._ttl, value)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max:
            self._entries.popitem(last=False)

    def invalidate(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


CACHE = ReadCache()
