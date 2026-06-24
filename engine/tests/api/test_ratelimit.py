"""Sprint 3 — Per-session sliding-window rate limiter."""
import uuid
import pytest
from app.api.rate_limiter import check_session_rate_limit, _session_hits


@pytest.fixture(autouse=True)
def clear_session_hits():
    """Isolate each test by flushing the in-memory hit counters."""
    _session_hits.clear()
    yield
    _session_hits.clear()


@pytest.mark.asyncio
async def test_within_limit_returns_true():
    """First N calls within the limit must all return True."""
    sid = str(uuid.uuid4())
    limit = 3
    for _ in range(limit):
        assert await check_session_rate_limit(sid, limit=limit, window_seconds=60) is True


@pytest.mark.asyncio
async def test_exceeding_limit_returns_false():
    """The (limit+1)-th call within the window must return False."""
    sid = str(uuid.uuid4())
    limit = 3
    for _ in range(limit):
        await check_session_rate_limit(sid, limit=limit, window_seconds=60)

    result = await check_session_rate_limit(sid, limit=limit, window_seconds=60)
    assert result is False


@pytest.mark.asyncio
async def test_different_sessions_are_independent():
    """Exhausting one session's limit must not affect a different session."""
    limit = 3
    sid_a = str(uuid.uuid4())
    sid_b = str(uuid.uuid4())

    # Exhaust sid_a
    for _ in range(limit):
        await check_session_rate_limit(sid_a, limit=limit, window_seconds=60)
    assert await check_session_rate_limit(sid_a, limit=limit, window_seconds=60) is False

    # sid_b should still be fresh
    assert await check_session_rate_limit(sid_b, limit=limit, window_seconds=60) is True


@pytest.mark.asyncio
async def test_expired_hits_are_evicted():
    """Hits older than window_seconds must be evicted and not count against the limit."""
    import time
    sid = str(uuid.uuid4())
    limit = 2

    # Manually plant stale timestamps directly
    _session_hits[sid] = [time.monotonic() - 120]  # 2 minutes ago

    # Window is 60s — the stale hit should be evicted, freeing up the slot
    assert await check_session_rate_limit(sid, limit=limit, window_seconds=60) is True
    assert await check_session_rate_limit(sid, limit=limit, window_seconds=60) is True
    # Now at limit
    assert await check_session_rate_limit(sid, limit=limit, window_seconds=60) is False


@pytest.mark.asyncio
async def test_limit_of_one():
    """A limit of 1 should allow exactly one call per window."""
    sid = str(uuid.uuid4())
    assert await check_session_rate_limit(sid, limit=1, window_seconds=60) is True
    assert await check_session_rate_limit(sid, limit=1, window_seconds=60) is False
