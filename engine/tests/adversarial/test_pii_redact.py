"""Adversarial tests verifying that Langfuse dual-write never leaks raw PII.

Sprint 1 fix: AuditLogger._langfuse_log now calls hash_query() on both
query and spl fields before forwarding to Langfuse.

These tests mount a mock Langfuse and capture the exact metadata dict
that would be sent, then assert:
  - Raw query text is absent
  - The value matches the expected hash
  - SPL is also hashed/not-raw
  - The local audit file still stores full unredacted data (compliance)
  - A Langfuse failure never propagates or breaks local audit logging
"""
import json
import pytest
from unittest.mock import MagicMock, patch
from app.pipeline.audit_logger import AuditLogger, AuditEntry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RAW_QUERY = "SELECT * FROM users WHERE name='John Smith'"
_RAW_SPL = "index=myindex sourcetype=auth earliest=-1d"


def _make_entry(
    query: str = _RAW_QUERY,
    spl: str = _RAW_SPL,
) -> AuditEntry:
    return AuditEntry(
        timestamp=0.0,
        session_id="sess-123",
        query=query,
        spl=spl,
        result_count=5,
        execution_time_ms=100,
        trace_id="abc123",
    )


def _make_logger_and_capture(tmp_path):
    """Return (logger, mock_langfuse, captured_metadata_list).

    captured_metadata_list is a list that gets one dict appended per lf.event() call.
    """
    mock_lf = MagicMock()
    captured: list[dict] = []

    def capture_event(**kwargs):
        captured.append(dict(kwargs.get("metadata", {})))

    mock_lf.event.side_effect = capture_event
    logger = AuditLogger(log_path=str(tmp_path / "audit.log"))
    return logger, mock_lf, captured


# ---------------------------------------------------------------------------
# PII redaction — raw query must not reach Langfuse
# ---------------------------------------------------------------------------

def test_langfuse_never_receives_raw_query(tmp_path):
    """Raw query text must not appear in any Langfuse event metadata value."""
    logger, mock_lf, captured = _make_logger_and_capture(tmp_path)

    with patch("app.observability.langfuse_client.get_langfuse", return_value=mock_lf):
        logger.log(_make_entry())

    assert mock_lf.event.called, "Langfuse event was never called"
    metadata = captured[0]
    assert _RAW_QUERY not in str(metadata.get("query", "")), (
        "Raw query found in Langfuse metadata — PII leak!"
    )
    # Also check no raw query sneaks in anywhere else in the metadata blob
    assert _RAW_QUERY not in json.dumps(metadata), (
        "Raw query text found somewhere in Langfuse metadata payload — PII leak!"
    )


def test_langfuse_receives_hashed_query(tmp_path):
    """The query field sent to Langfuse must equal hash_query(raw_query)."""
    from app.observability.redact import hash_query

    logger, mock_lf, captured = _make_logger_and_capture(tmp_path)
    expected_hash = hash_query(_RAW_QUERY)

    with patch("app.observability.langfuse_client.get_langfuse", return_value=mock_lf):
        logger.log(_make_entry())

    metadata = captured[0]
    assert metadata.get("query") == expected_hash, (
        f"Expected hashed query '{expected_hash}', got '{metadata.get('query')}'"
    )


# ---------------------------------------------------------------------------
# PII redaction — raw SPL must not reach Langfuse
# ---------------------------------------------------------------------------

def test_langfuse_spl_not_raw(tmp_path):
    """SPL must be hashed, not sent raw."""
    logger, mock_lf, captured = _make_logger_and_capture(tmp_path)

    with patch("app.observability.langfuse_client.get_langfuse", return_value=mock_lf):
        logger.log(_make_entry(spl=_RAW_SPL))

    metadata = captured[0]
    assert metadata.get("spl") != _RAW_SPL, (
        "Raw SPL sent to Langfuse — PII leak!"
    )
    assert _RAW_SPL not in json.dumps(metadata), (
        "Raw SPL text found somewhere in Langfuse metadata payload — PII leak!"
    )


def test_langfuse_receives_hashed_spl(tmp_path):
    """The spl field sent to Langfuse must equal hash_query(raw_spl)."""
    from app.observability.redact import hash_query

    logger, mock_lf, captured = _make_logger_and_capture(tmp_path)
    expected_hash = hash_query(_RAW_SPL)

    with patch("app.observability.langfuse_client.get_langfuse", return_value=mock_lf):
        logger.log(_make_entry(spl=_RAW_SPL))

    metadata = captured[0]
    assert metadata.get("spl") == expected_hash, (
        f"Expected hashed SPL '{expected_hash}', got '{metadata.get('spl')}'"
    )


def test_langfuse_spl_explanation_empty(tmp_path):
    """spl_explanation must be blanked before sending to Langfuse (may contain raw SPL context)."""
    logger, mock_lf, captured = _make_logger_and_capture(tmp_path)

    entry = _make_entry()
    entry.spl_explanation = "This query searches for John Smith in the auth log."

    with patch("app.observability.langfuse_client.get_langfuse", return_value=mock_lf):
        logger.log(entry)

    metadata = captured[0]
    assert metadata.get("spl_explanation") == "", (
        "spl_explanation was not blanked before Langfuse — potential PII leak!"
    )


# ---------------------------------------------------------------------------
# Compliance — local audit file must remain fully unredacted
# ---------------------------------------------------------------------------

def test_local_audit_log_has_full_query(tmp_path):
    """The file-based audit log must store the full, unredacted query for compliance."""
    log_path = tmp_path / "audit.log"
    logger = AuditLogger(log_path=str(log_path))
    mock_lf = MagicMock()

    with patch("app.observability.langfuse_client.get_langfuse", return_value=mock_lf):
        logger.log(_make_entry())

    lines = log_path.read_text().strip().splitlines()
    assert len(lines) == 1, "Expected exactly one audit log line"
    data = json.loads(lines[0])
    assert data["query"] == _RAW_QUERY, (
        "Audit file must contain the full, unredacted query for compliance"
    )


def test_local_audit_log_has_full_spl(tmp_path):
    """The file-based audit log must store the full, unredacted SPL."""
    log_path = tmp_path / "audit.log"
    logger = AuditLogger(log_path=str(log_path))
    mock_lf = MagicMock()

    with patch("app.observability.langfuse_client.get_langfuse", return_value=mock_lf):
        logger.log(_make_entry())

    data = json.loads((tmp_path / "audit.log").read_text().strip())
    assert data["spl"] == _RAW_SPL, (
        "Audit file must contain the full, unredacted SPL for compliance"
    )


def test_local_audit_log_has_session_and_trace(tmp_path):
    """Audit file should retain session_id and trace_id for correlation."""
    log_path = tmp_path / "audit.log"
    logger = AuditLogger(log_path=str(log_path))
    mock_lf = MagicMock()

    with patch("app.observability.langfuse_client.get_langfuse", return_value=mock_lf):
        logger.log(_make_entry())

    data = json.loads((tmp_path / "audit.log").read_text().strip())
    assert data["session_id"] == "sess-123"
    assert data["trace_id"] == "abc123"


# ---------------------------------------------------------------------------
# Resilience — Langfuse failure must never break audit logging
# ---------------------------------------------------------------------------

def test_langfuse_failure_does_not_break_audit(tmp_path):
    """A Langfuse RuntimeError must be swallowed and not propagate."""
    mock_lf = MagicMock()
    mock_lf.event.side_effect = RuntimeError("Langfuse unavailable")
    log_path = tmp_path / "audit.log"
    logger = AuditLogger(log_path=str(log_path))

    with patch("app.observability.langfuse_client.get_langfuse", return_value=mock_lf):
        logger.log(_make_entry())  # must not raise

    assert log_path.exists(), "Audit file must exist even when Langfuse fails"


def test_langfuse_failure_local_log_still_written(tmp_path):
    """When Langfuse fails, the local audit log must still contain the entry."""
    mock_lf = MagicMock()
    mock_lf.event.side_effect = Exception("Network error")
    log_path = tmp_path / "audit.log"
    logger = AuditLogger(log_path=str(log_path))

    with patch("app.observability.langfuse_client.get_langfuse", return_value=mock_lf):
        logger.log(_make_entry())

    content = log_path.read_text().strip()
    assert content, "Audit file must be non-empty even when Langfuse fails"
    data = json.loads(content)
    assert data["query"] == _RAW_QUERY


def test_langfuse_not_called_when_returns_none(tmp_path):
    """If get_langfuse() returns None (not configured), no crash and local log is written."""
    log_path = tmp_path / "audit.log"
    logger = AuditLogger(log_path=str(log_path))

    with patch("app.observability.langfuse_client.get_langfuse", return_value=None):
        logger.log(_make_entry())  # must not raise

    data = json.loads((tmp_path / "audit.log").read_text().strip())
    assert data["query"] == _RAW_QUERY
