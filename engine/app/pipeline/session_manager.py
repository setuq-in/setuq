import asyncio
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class ConversationTurn:
    query: str
    spl: str
    result_count: int
    summary: str


@dataclass
class ConversationSession:
    session_id: str
    turns: list[ConversationTurn] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)


_SESSION_TTL_SECONDS = 3600
_CLEANUP_INTERVAL_SECONDS = 300


class SessionManager:
    def __init__(self, max_turns: int = 10, max_sessions: int = 1000):
        self._sessions: dict[str, ConversationSession] = {}
        self._max_turns = max_turns
        self._max_sessions = max_sessions
        # Short-held lock for registry mutations only
        self._registry_lock = asyncio.Lock()
        # Per-session locks to avoid global serialisation
        self._session_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._cleanup_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start_cleanup_task(self) -> None:
        """Schedule background cleanup. Call once at app startup."""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.ensure_future(self._cleanup_loop())

    async def stop_cleanup_task(self) -> None:
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(_CLEANUP_INTERVAL_SECONDS)
            await self._evict_expired()

    async def _evict_expired(self) -> None:
        now = time.time()
        async with self._registry_lock:
            expired = [
                sid for sid, s in self._sessions.items()
                if now - s.last_active > _SESSION_TTL_SECONDS
            ]
            for sid in expired:
                del self._sessions[sid]
                self._session_locks.pop(sid, None)

    def _evict_lru_if_needed(self) -> None:
        """Must be called under _registry_lock."""
        if len(self._sessions) >= self._max_sessions:
            lru_id = min(self._sessions, key=lambda sid: self._sessions[sid].last_active)
            del self._sessions[lru_id]
            self._session_locks.pop(lru_id, None)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_or_create(self, session_id: str | None) -> tuple[str, ConversationSession]:
        """Return existing session or create new one. Unknown caller-supplied IDs create a new session."""
        async with self._registry_lock:
            if session_id and session_id in self._sessions:
                self._sessions[session_id].last_active = time.time()
                return session_id, self._sessions[session_id]
            # Unknown or missing session_id -> always create fresh (prevents fixation)
            new_id = str(uuid.uuid4())
            self._evict_lru_if_needed()
            session = ConversationSession(session_id=new_id)
            self._sessions[new_id] = session
            return new_id, session

    async def append_turn(self, session_id: str, turn: ConversationTurn) -> None:
        # Hold registry_lock for the full mutation — prevents eviction racing the write.
        async with self._registry_lock:
            session = self._sessions.get(session_id)
            if session is None:
                return
            session.turns.append(turn)
            if len(session.turns) > self._max_turns:
                session.turns = session.turns[-self._max_turns:]
            session.last_active = time.time()

    async def build_history_messages(self, session_id: str) -> list[dict]:
        async with self._registry_lock:
            session = self._sessions.get(session_id)
            if session is None:
                return []
            turns_snapshot = list(session.turns)
        messages = []
        for turn in turns_snapshot:
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
