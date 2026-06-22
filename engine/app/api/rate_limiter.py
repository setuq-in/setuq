from __future__ import annotations
import asyncio
import time
from collections import OrderedDict
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, enabled=True)

# Per-session sliding window counters: session_id -> list of timestamps
# Bounded OrderedDict evicts oldest session on overflow (LRU by insertion order).
_MAX_TRACKED_SESSIONS = 10_000
_session_hits: OrderedDict[str, list[float]] = OrderedDict()
_session_lock = asyncio.Lock()


def set_limiter_enabled(enabled: bool) -> None:
    limiter._enabled = enabled  # type: ignore[attr-defined]


async def check_session_rate_limit(session_id: str, limit: int, window_seconds: int = 60) -> bool:
    """Return True if within limit, False if exceeded. Thread-safe via asyncio lock."""
    now = time.monotonic()
    cutoff = now - window_seconds
    async with _session_lock:
        hits = _session_hits.get(session_id, [])
        hits = [t for t in hits if t > cutoff]
        if len(hits) >= limit:
            _session_hits[session_id] = hits
            return False
        hits.append(now)
        _session_hits[session_id] = hits
        _session_hits.move_to_end(session_id)
        # Evict oldest session if over cap
        while len(_session_hits) > _MAX_TRACKED_SESSIONS:
            _session_hits.popitem(last=False)
        return True
