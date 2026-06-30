"""Sprint 2 — Concurrent audit writes: thread safety, valid JSON output."""
import asyncio
import concurrent.futures
import json
import time
import pytest

import app.pipeline.audit_logger as al
from app.pipeline.audit_logger import AuditLogger, AuditEntry


@pytest.fixture(autouse=True)
def reset_singleton():
    """Isolate each test by clearing the module-level singleton."""
    al._instance = None
    yield
    al._instance = None


def _make_entry(i: int) -> AuditEntry:
    return AuditEntry(
        timestamp=time.time(),
        session_id=f"sess-{i}",
        query=f"query {i}",
        spl="index=myindex earliest=-1d | stats count by host",
        result_count=i,
        execution_time_ms=10 + i,
    )


# ---------------------------------------------------------------------------
# Thread-based concurrent writes
# ---------------------------------------------------------------------------

def test_concurrent_writes_all_valid_json(tmp_path):
    """20 concurrent log() calls from threads must produce exactly 20 valid JSON lines."""
    log_path = str(tmp_path / "concurrent.log")
    logger = AuditLogger(log_path=log_path)

    def write(i: int):
        logger.log(_make_entry(i))

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        list(executor.map(write, range(20)))

    logger.stop()  # flush background listener before reading

    lines = (tmp_path / "concurrent.log").read_text().strip().splitlines()
    assert len(lines) == 20, f"Expected 20 lines, got {len(lines)}"
    for line in lines:
        data = json.loads(line)  # must parse — no interleaved partial writes
        assert "session_id" in data
        assert "query" in data


def test_high_concurrency_no_duplicate_or_missing(tmp_path):
    """50 entries from 20 threads must all appear exactly once."""
    log_path = str(tmp_path / "high_concurrency.log")
    logger = AuditLogger(log_path=log_path)

    def write(i: int):
        logger.log(_make_entry(i))

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        list(executor.map(write, range(50)))

    logger.stop()

    lines = (tmp_path / "high_concurrency.log").read_text().strip().splitlines()
    assert len(lines) == 50, f"Expected 50 lines, got {len(lines)}"

    session_ids = {json.loads(line)["session_id"] for line in lines}
    expected = {f"sess-{i}" for i in range(50)}
    assert session_ids == expected, "Some writes were lost or duplicated"


# ---------------------------------------------------------------------------
# Async concurrent writes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_async_concurrent_writes_all_valid_json(tmp_path):
    """20 concurrent async log() calls must produce 20 valid JSON lines."""
    log_path = str(tmp_path / "async_concurrent.log")
    logger = AuditLogger(log_path=log_path)

    async def write(i: int):
        # log() is synchronous internally (uses QueueHandler) — safe to call from coroutines
        logger.log(_make_entry(i))

    await asyncio.gather(*[write(i) for i in range(20)])
    logger.stop()

    lines = (tmp_path / "async_concurrent.log").read_text().strip().splitlines()
    assert len(lines) == 20, f"Expected 20 lines, got {len(lines)}"
    for line in lines:
        data = json.loads(line)
        assert "session_id" in data
        assert "query" in data


# ---------------------------------------------------------------------------
# Each line is complete JSON (no interleaved writes)
# ---------------------------------------------------------------------------

def test_no_interleaved_writes(tmp_path):
    """Verify that no line is a partial JSON fragment from interleaved writes."""
    log_path = str(tmp_path / "interleave_check.log")
    logger = AuditLogger(log_path=log_path)

    def write(i: int):
        # Use a long SPL to increase chance of exposing interleaving if lock is missing
        entry = AuditEntry(
            timestamp=time.time(),
            session_id=f"sess-{i}",
            query="x" * 200,
            spl="index=main | eval a=1 | eval b=2 | stats count by host, source, sourcetype",
            result_count=i,
            execution_time_ms=i,
        )
        logger.log(entry)

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        list(executor.map(write, range(30)))

    logger.stop()

    raw = (tmp_path / "interleave_check.log").read_text()
    lines = raw.strip().splitlines()
    assert len(lines) == 30

    for idx, line in enumerate(lines):
        try:
            json.loads(line)
        except json.JSONDecodeError as exc:
            pytest.fail(
                f"Line {idx} is not valid JSON (possible interleaved write): {exc}\nLine: {line[:200]!r}"
            )
