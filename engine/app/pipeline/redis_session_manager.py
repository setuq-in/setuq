from __future__ import annotations
import json
import logging
import time
import uuid
from app.pipeline.session_manager import ConversationSession, ConversationTurn

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

    async def get_or_create(self, session_id: str | None) -> tuple[str, ConversationSession]:
        if session_id:
            raw = await self._redis.get(_session_key(session_id))
            if raw:
                data = json.loads(raw)
                session = self._deserialize(data)
                session.last_active = time.time()
                await self._save(session)
                return session_id, session
        new_id = str(uuid.uuid4())
        session = ConversationSession(session_id=new_id)
        await self._save(session)
        return new_id, session

    async def append_turn(self, session_id: str, turn: ConversationTurn) -> None:
        raw = await self._redis.get(_session_key(session_id))
        if not raw:
            return
        data = json.loads(raw)
        session = self._deserialize(data)
        session.turns.append(turn)
        if len(session.turns) > self._max_turns:
            session.turns = session.turns[-self._max_turns:]
        session.last_active = time.time()
        await self._save(session)

    async def build_history_messages(self, session_id: str) -> list[dict]:
        raw = await self._redis.get(_session_key(session_id))
        if not raw:
            return []
        session = self._deserialize(json.loads(raw))
        messages = []
        for turn in session.turns:
            messages.append({"role": "user", "content": turn.query})
            messages.append({
                "role": "assistant",
                "content": (
                    f"SPL: {turn.spl}\n"
                    f"Results: {turn.result_count} events\n"
                    f"Summary: {turn.summary}"
                ),
            })
        return messages

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


def create_session_manager(settings, max_turns: int = 10):
    """Factory: Redis if REDIS_URL set, else in-memory."""
    if settings.REDIS_URL:
        try:
            import redis.asyncio as aioredis
            client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
            _logger.info("Using Redis session store at %s", settings.REDIS_URL)
            return RedisSessionManager(redis_client=client, max_turns=max_turns)
        except Exception as exc:
            _logger.warning("Redis unavailable (%s) — falling back to in-memory sessions", exc)

    from app.pipeline.session_manager import SessionManager
    return SessionManager(max_turns=max_turns)
