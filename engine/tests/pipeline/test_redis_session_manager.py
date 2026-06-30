"""Tests for RedisSessionManager using fakeredis."""
import pytest
from app.pipeline.redis_session_manager import RedisSessionManager
from app.pipeline.session_manager import ConversationTurn

try:
    import fakeredis.aioredis as fakeredis
    _HAS_FAKEREDIS = True
except ImportError:
    _HAS_FAKEREDIS = False

pytestmark = pytest.mark.skipif(not _HAS_FAKEREDIS, reason="fakeredis not installed")


@pytest.fixture
def redis_client():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def manager(redis_client):
    return RedisSessionManager(redis_client=redis_client, max_turns=5)


@pytest.mark.asyncio
async def test_get_or_create_new_session(manager):
    sid, session = await manager.get_or_create(None)
    assert sid is not None
    assert session.session_id == sid


@pytest.mark.asyncio
async def test_get_or_create_existing_session(manager):
    sid, _ = await manager.get_or_create(None)
    sid2, session2 = await manager.get_or_create(sid)
    assert sid2 == sid


@pytest.mark.asyncio
async def test_unknown_session_id_creates_fresh(manager):
    sid, session = await manager.get_or_create("fake-unknown-id")
    assert sid != "fake-unknown-id"


@pytest.mark.asyncio
async def test_append_turn_and_history(manager):
    sid, _ = await manager.get_or_create(None)
    await manager.append_turn(sid, ConversationTurn(
        query="test query", spl="index=main", result_count=5, summary="5 results"
    ))
    history = await manager.build_history_messages(sid)
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert "test query" in history[0]["content"]


@pytest.mark.asyncio
async def test_max_turns_enforced(manager):
    sid, _ = await manager.get_or_create(None)
    for i in range(7):
        await manager.append_turn(sid, ConversationTurn(
            query=f"q{i}", spl="index=main", result_count=i, summary=f"s{i}"
        ))
    history = await manager.build_history_messages(sid)
    # max_turns=5, each turn = 2 messages
    assert len(history) == 10


@pytest.mark.asyncio
async def test_append_turn_unknown_session_is_noop(manager):
    await manager.append_turn("nonexistent-sid", ConversationTurn(
        query="q", spl="s", result_count=0, summary="s"
    ))
    history = await manager.build_history_messages("nonexistent-sid")
    assert history == []


@pytest.mark.asyncio
async def test_start_stop_cleanup_noop(manager):
    manager.start_cleanup_task()
    await manager.stop_cleanup_task()
