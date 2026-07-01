import time
import pytest
from app.pipeline.session_manager import SessionManager, ConversationTurn, ConversationSession


def make_turn(n: int = 1) -> ConversationTurn:
    return ConversationTurn(
        query=f"query {n}",
        spl=f"index=main | stats count{n}",
        result_count=n,
        summary=f"Found {n} events",
    )


class TestGetOrCreate:
    @pytest.mark.asyncio
    async def test_creates_new_session_when_none(self):
        mgr = SessionManager()
        sid, session = await mgr.get_or_create(None)
        assert sid
        assert isinstance(session, ConversationSession)
        assert session.turns == []

    @pytest.mark.asyncio
    async def test_returns_existing_session(self):
        mgr = SessionManager()
        sid1, _ = await mgr.get_or_create(None)
        sid2, session2 = await mgr.get_or_create(sid1)
        assert sid1 == sid2
        assert session2.session_id == sid1

    @pytest.mark.asyncio
    async def test_creates_new_for_unknown_id(self):
        """Unknown caller-supplied IDs must NOT be adopted (session fixation prevention)."""
        mgr = SessionManager()
        sid, session = await mgr.get_or_create("nonexistent-id")
        assert sid != "nonexistent-id", "Session fixation: unknown ID must not be reused"
        assert session.turns == []


class TestAppendTurn:
    @pytest.mark.asyncio
    async def test_appends_turn(self):
        mgr = SessionManager()
        sid, _ = await mgr.get_or_create(None)
        await mgr.append_turn(sid, make_turn(1))
        _, session = await mgr.get_or_create(sid)
        assert len(session.turns) == 1
        assert session.turns[0].query == "query 1"

    @pytest.mark.asyncio
    async def test_enforces_max_turns_window(self):
        mgr = SessionManager(max_turns=3)
        sid, _ = await mgr.get_or_create(None)
        for i in range(5):
            await mgr.append_turn(sid, make_turn(i))
        _, session = await mgr.get_or_create(sid)
        assert len(session.turns) == 3
        assert session.turns[0].query == "query 2"
        assert session.turns[2].query == "query 4"


class TestBuildHistoryMessages:
    @pytest.mark.asyncio
    async def test_builds_user_assistant_pairs(self):
        mgr = SessionManager()
        sid, _ = await mgr.get_or_create(None)
        await mgr.append_turn(sid, make_turn(1))
        messages = await mgr.build_history_messages(sid)
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "query 1"
        assert messages[1]["role"] == "assistant"
        assert "SPL:" in messages[1]["content"]
        assert "Results: 1 events" in messages[1]["content"]
        assert "Summary: Found 1 events" in messages[1]["content"]

    @pytest.mark.asyncio
    async def test_returns_empty_for_unknown_session(self):
        mgr = SessionManager()
        assert await mgr.build_history_messages("nope") == []


class TestCleanup:
    @pytest.mark.asyncio
    async def test_removes_expired_sessions(self):
        mgr = SessionManager()
        sid, session = await mgr.get_or_create(None)
        # Backdate the session so _evict_expired will remove it
        session.last_active = time.time() - 7200
        await mgr._evict_expired()
        # Session should be gone; requesting it again creates a brand-new session
        assert sid not in mgr._sessions
        sid2, _ = await mgr.get_or_create(sid)
        # Fixation prevention: the new session gets a fresh UUID, not the old one
        assert sid2 != sid

    @pytest.mark.asyncio
    async def test_lru_eviction_at_cap(self):
        mgr = SessionManager(max_sessions=3)
        id1, _ = await mgr.get_or_create(None)
        id2, _ = await mgr.get_or_create(None)
        id3, _ = await mgr.get_or_create(None)
        assert len(mgr._sessions) == 3
        # 4th session must evict the LRU entry
        id4, _ = await mgr.get_or_create(None)
        assert len(mgr._sessions) == 3
        assert id4 in mgr._sessions
