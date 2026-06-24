"""Sprint 2 — Session hardening: concurrency, LRU eviction, fixation prevention."""
import asyncio
import time
import pytest
from app.pipeline.session_manager import SessionManager, ConversationTurn


def _turn(i: int) -> ConversationTurn:
    return ConversationTurn(query=f"q{i}", spl=f"s{i}", result_count=i, summary=f"sum{i}")


# ---------------------------------------------------------------------------
# Concurrent appends — no data corruption
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_appends_no_corruption():
    """10 concurrent append_turn calls must produce exactly 10 turns (no race corruption)."""
    sm = SessionManager(max_turns=20)
    sid, _ = await sm.get_or_create(None)

    async def append_one(i: int):
        await sm.append_turn(sid, _turn(i))

    await asyncio.gather(*[append_one(i) for i in range(10)])
    history = await sm.build_history_messages(sid)
    # Each turn produces 2 messages (user + assistant)
    assert len(history) == 20, f"Expected 20 messages, got {len(history)}"


# ---------------------------------------------------------------------------
# LRU eviction at capacity
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lru_eviction_at_cap():
    """When max_sessions is reached, the LRU session must be evicted."""
    sm = SessionManager(max_turns=5, max_sessions=3)

    id1, _ = await sm.get_or_create(None)
    await asyncio.sleep(0.01)
    id2, _ = await sm.get_or_create(None)
    await asyncio.sleep(0.01)
    id3, _ = await sm.get_or_create(None)
    assert len(sm._sessions) == 3

    # 4th session should evict the LRU (id1)
    id4, _ = await sm.get_or_create(None)
    assert len(sm._sessions) == 3
    assert id1 not in sm._sessions, "LRU session (id1) should have been evicted"
    assert id4 in sm._sessions


# ---------------------------------------------------------------------------
# Session fixation prevention
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_client_supplied_unknown_id_creates_fresh():
    """An unknown session_id supplied by the client must NOT be adopted."""
    sm = SessionManager()
    returned_id, _ = await sm.get_or_create("attacker-controlled-id")
    assert returned_id != "attacker-controlled-id", (
        "Session fixation: unknown client-supplied ID must not be used as the session ID"
    )
    assert returned_id in sm._sessions


@pytest.mark.asyncio
async def test_known_session_id_reused():
    """A valid existing session_id must be returned unchanged."""
    sm = SessionManager()
    orig_id, _ = await sm.get_or_create(None)
    returned_id, _ = await sm.get_or_create(orig_id)
    assert returned_id == orig_id


# ---------------------------------------------------------------------------
# TTL expiry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_expired_sessions_evicted():
    """Sessions older than TTL must be evicted by _evict_expired."""
    sm = SessionManager()
    sid, _ = await sm.get_or_create(None)
    # Backdate the session to 2 hours ago
    sm._sessions[sid].last_active = time.time() - 7200
    await sm._evict_expired()
    assert sid not in sm._sessions


# ---------------------------------------------------------------------------
# Cross-session isolation — no global lock deadlock
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multiple_sessions_dont_block_each_other():
    """Concurrent operations on different sessions must not deadlock."""
    sm = SessionManager(max_turns=50)
    id1, _ = await sm.get_or_create(None)
    id2, _ = await sm.get_or_create(None)

    async def heavy_session(sid: str):
        for i in range(5):
            await sm.append_turn(sid, _turn(i))

    # Must complete within 5 seconds; any deadlock will timeout
    await asyncio.wait_for(
        asyncio.gather(heavy_session(id1), heavy_session(id2)),
        timeout=5.0,
    )

    h1 = await sm.build_history_messages(id1)
    h2 = await sm.build_history_messages(id2)
    assert len(h1) == 10
    assert len(h2) == 10
