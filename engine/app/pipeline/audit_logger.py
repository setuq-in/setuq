from __future__ import annotations
import asyncio
import contextlib
import json
import logging
import logging.handlers
import queue
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict
from pathlib import Path

_LANGFUSE_QUEUE_MAX = 500


class _JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # record.msg is already JSON string from log() method
        return str(record.msg)


@dataclass
class AuditEntry:
    timestamp: float
    session_id: str
    query: str
    spl: str
    result_count: int
    execution_time_ms: int
    spl_explanation: str = ""
    actions_suggested: list[dict] = field(default_factory=list)
    guardrail_violations: list[str] = field(default_factory=list)
    trace_id: str = ""
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    rejected: bool = False
    rejection_reason: str = ""


# Module-level singleton — initialized once via init_audit_logger()
_instance: AuditLogger | None = None


def init_audit_logger(log_path: str = "audit.log") -> "AuditLogger":
    global _instance
    if _instance is not None:
        return _instance
    _instance = AuditLogger(log_path=log_path)
    return _instance


def get_audit_logger() -> "AuditLogger":
    if _instance is None:
        return init_audit_logger()
    return _instance


class AuditLogger:
    def __init__(self, log_path: str = "audit.log"):
        # Ensure parent directory exists
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)

        self._log_queue: queue.Queue = queue.Queue(maxsize=10_000)

        logger_name = f"setuq.audit.{uuid.uuid4().hex[:8]}"
        self._logger = logging.getLogger(logger_name)
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False

        # QueueHandler is non-blocking — puts record on queue
        queue_handler = logging.handlers.QueueHandler(self._log_queue)
        self._logger.addHandler(queue_handler)

        # QueueListener processes queue in background thread — single writer
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(_JSONFormatter())
        self._listener = logging.handlers.QueueListener(
            self._log_queue, file_handler, respect_handler_level=True
        )
        self._listener.start()
        # Langfuse async path — initialized by attach_loop() during lifespan startup
        self._lf_queue: asyncio.Queue | None = None
        self._lf_task: asyncio.Task | None = None
        self._lf_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="audit-lf")

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Wire async Langfuse path. Call once during lifespan startup."""
        self._lf_queue = asyncio.Queue(maxsize=_LANGFUSE_QUEUE_MAX)
        self._lf_task = loop.create_task(self._langfuse_worker())

    def log(self, entry: AuditEntry) -> None:
        self._logger.info(json.dumps(asdict(entry)))
        self._langfuse_log(entry)

    def _langfuse_log(self, entry: AuditEntry) -> None:
        if self._lf_queue is not None:
            try:
                self._lf_queue.put_nowait(entry)
            except asyncio.QueueFull:
                pass  # drop under backpressure
        else:
            # Sync fallback before loop is attached (startup / tests)
            self._lf_executor.submit(self._send_langfuse_event, entry)

    async def _langfuse_worker(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            entry = await self._lf_queue.get()
            try:
                await asyncio.wait_for(
                    loop.run_in_executor(self._lf_executor, self._send_langfuse_event, entry),
                    timeout=5.0,
                )
            except (asyncio.TimeoutError, Exception):
                pass

    def _send_langfuse_event(self, entry: AuditEntry) -> None:
        try:
            from app.observability.langfuse_client import get_langfuse
            from app.observability.redact import hash_query, scrub_dict
            lf = get_langfuse()
            if lf is None:
                return
            data = asdict(entry)
            data["query"] = hash_query(data["query"])
            data["spl"] = hash_query(data["spl"])
            data["spl_explanation"] = ""
            data = scrub_dict(data)
            lf.event(
                trace_id=entry.trace_id or None,
                name="pipeline.audit",
                metadata=data,
            )
        except Exception:
            pass

    async def aclose(self) -> None:
        """Async teardown — cancels worker, flushes file handler."""
        if self._lf_task and not self._lf_task.done():
            self._lf_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._lf_task
        self._lf_executor.shutdown(wait=False)
        self.stop()

    def stop(self) -> None:
        """Flush and stop the background file listener. Idempotent."""
        try:
            self._listener.stop()
        except RuntimeError:
            pass  # already stopped
