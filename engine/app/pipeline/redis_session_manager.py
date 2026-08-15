from __future__ import annotations
import json
import logging
import time
import uuid
from app.pipeline.session_manager import (
    ConversationSession,
    ConversationTurn,
    SessionManager,
    turns_to_messages,
)

_logger = logging.getLogger("setuq.redis_session")

_KEY_PREFIX = "setuq:session:"
_TTL_SECONDS = 3600


def _session_key(session_id: str) -> str:
    return f"{_KEY_PREFIX}{session_id}"


class RedisSessionManager:
    """Redis-backed session manager. Same interface as SessionManager."""

    def __init__(self, redis_client, max_turns: int = 10) -> None:
        self._redis = redis_client
        self._max_turns = max_turns

    # ------------------------------------------------------------------
    # Lifecycle stubs (cleanup handled by Redis TTL)
    # ------------------------------------------------------------------

    def start_cleanup_task(self) -> None:
        pass

    async def stop_cleanup_task(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def _load(self, session_id: str) -> ConversationSession | None:
        raw = await self._redis.get(_session_key(session_id))
        return self._deserialize(json.loads(raw)) if raw else None

    async def get_or_create(self, session_id: str | None) -> tuple[str, ConversationSession]:
        if session_id:
            session = await self._load(session_id)
            if session is not None:
                session.last_active = time.time()
                await self._save(session)
                return session_id, session
        new_id = str(uuid.uuid4())
        session = ConversationSession(session_id=new_id)
        await self._save(session)
        return new_id, session

    async def append_turn(self, session_id: str, turn: ConversationTurn) -> None:
        session = await self._load(session_id)
        if session is None:
            return
        session.turns.append(turn)
        if len(session.turns) > self._max_turns:
            session.turns = session.turns[-self._max_turns:]
        session.last_active = time.time()
        await self._save(session)

    async def build_history_messages(self, session_id: str) -> list[dict]:
        session = await self._load(session_id)
        if session is None:
            return []
        return turns_to_messages(session.turns)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    async def _save(self, session: ConversationSession) -> None:
        data = {
            "session_id": session.session_id,
            "created_at": session.created_at,
            "last_active": session.last_active,
            "turns": [
                {
                    "query": t.query,
                    "spl": t.spl,
                    "result_count": t.result_count,
                    "summary": t.summary,
                }
                for t in session.turns
            ],
        }
        await self._redis.set(
            _session_key(session.session_id),
            json.dumps(data),
            ex=_TTL_SECONDS,
        )

    @staticmethod
    def _deserialize(data: dict) -> ConversationSession:
        session = ConversationSession(session_id=data["session_id"])
        session.created_at = data.get("created_at", time.time())
        session.last_active = data.get("last_active", time.time())
        session.turns = [
            ConversationTurn(
                query=t["query"],
                spl=t["spl"],
                result_count=t["result_count"],
                summary=t["summary"],
            )
            for t in data.get("turns", [])
        ]
        return session


def _make_cache_client(settings):
    """Build an async RESP client. Backend is user's choice via CACHE_BACKEND.

    Both redis-py and valkey-py expose the same async `from_url` API and speak
    RESP, so the RedisSessionManager works unchanged with either.
    """
    backend = (getattr(settings, "CACHE_BACKEND", "redis") or "redis").lower()
    if backend == "valkey":
        import valkey.asyncio as valkey  # requires `pip install valkey`
        _logger.info("Using Valkey session store at %s", settings.REDIS_URL)
        return valkey.from_url(settings.REDIS_URL, decode_responses=True)
    import redis.asyncio as aioredis
    _logger.info("Using Redis session store at %s", settings.REDIS_URL)
    return aioredis.from_url(settings.REDIS_URL, decode_responses=True)


def create_session_manager(settings, max_turns: int = 10):
    """Factory: Redis/Valkey if REDIS_URL set, else in-memory."""
    if settings.REDIS_URL:
        try:
            client = _make_cache_client(settings)
            return RedisSessionManager(redis_client=client, max_turns=max_turns)
        except ImportError as exc:
            _logger.warning(
                "CACHE_BACKEND=%s selected but its client is not installed (%s) — "
                "falling back to in-memory sessions",
                getattr(settings, "CACHE_BACKEND", "redis"), exc,
            )
        except Exception as exc:
            _logger.warning("Cache backend unavailable (%s) — falling back to in-memory sessions", exc)

    return SessionManager(max_turns=max_turns)
