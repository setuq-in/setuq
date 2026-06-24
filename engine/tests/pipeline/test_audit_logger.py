import json
from pathlib import Path
import pytest
import time
from app.pipeline.audit_logger import AuditLogger, AuditEntry
import app.pipeline.audit_logger as al


@pytest.fixture
def audit_log_path(tmp_path, request):
    # Use unique filename per test to avoid conflicts
    return str(tmp_path / f"test_audit_{request.node.name}.log")


@pytest.fixture
def audit_logger(audit_log_path):
    # Reset singleton before each test
    al._instance = None
    logger = AuditLogger(log_path=audit_log_path)
    yield logger
    logger.stop()  # QueueListener.stop() blocks until queue is empty
    al._instance = None  # Reset for next test


def test_log_writes_json_entry(audit_logger, audit_log_path):
    entry = AuditEntry(
        timestamp=1700000000.0,
        session_id="sess-123",
        query="failed logins",
        spl="index=security | stats count",
        result_count=5,
        execution_time_ms=200,
        spl_explanation="Counts events in security index",
        actions_suggested=[{"action": "block_ip", "target": "10.0.0.1", "risk_level": "high"}],
        guardrail_violations=[],
    )
    audit_logger.log(entry)

    content = Path(audit_log_path).read_text().strip()
    parsed = json.loads(content)
    assert parsed["session_id"] == "sess-123"
    assert parsed["query"] == "failed logins"
    assert parsed["result_count"] == 5
    assert parsed["spl_explanation"] == "Counts events in security index"
    assert len(parsed["actions_suggested"]) == 1
    assert parsed["actions_suggested"][0]["action"] == "block_ip"


def test_log_multiple_entries(audit_logger, audit_log_path):
    for i in range(3):
        audit_logger.log(AuditEntry(
            timestamp=1700000000.0 + i,
            session_id=f"sess-{i}",
            query=f"query {i}",
            spl=f"index=main | stats count",
            result_count=i,
            execution_time_ms=100,
        ))

    audit_logger.stop()  # flush QueueListener before reading
    lines = Path(audit_log_path).read_text().strip().split("\n")
    assert len(lines) == 3
    for i, line in enumerate(lines):
        parsed = json.loads(line)
        assert parsed["session_id"] == f"sess-{i}"


def test_log_with_guardrail_violations(audit_logger, audit_log_path):
    entry = AuditEntry(
        timestamp=1700000000.0,
        session_id="sess-456",
        query="test",
        spl="index=bad | stats count",
        result_count=0,
        execution_time_ms=50,
        guardrail_violations=["Unknown index 'bad'", "No time range specified"],
    )
    audit_logger.log(entry)

    content = Path(audit_log_path).read_text().strip()
    parsed = json.loads(content)
    assert len(parsed["guardrail_violations"]) == 2
