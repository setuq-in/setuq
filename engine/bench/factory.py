from __future__ import annotations
from app.pipeline.audit_logger import AuditLogger, AuditEntry
from app.pipeline.orchestrator import PipelineOrchestrator
from tests.pipeline.test_orchestrator import _build_orchestrator


class CapturingAuditLogger(AuditLogger):
    def __init__(self, log_path: str) -> None:
        super().__init__(log_path=log_path)
        self.entries: list[AuditEntry] = []

    def log(self, entry: AuditEntry) -> None:
        self.entries.append(entry)
        super().log(entry)

    def last_cost(self) -> float:
        return (self.entries[-1].total_cost_usd if self.entries else 0.0) or 0.0

    def last_tokens(self) -> int:
        return (self.entries[-1].total_tokens if self.entries else 0) or 0


def build_bench_orchestrator(tmp_path, llm, splunk_return=None) -> tuple[PipelineOrchestrator, CapturingAuditLogger]:
    from tests.pipeline.test_orchestrator import _SENTINEL
    orch = _build_orchestrator(
        tmp_path,
        llm=llm,
        splunk_return=_SENTINEL if splunk_return is None else splunk_return,
    )
    audit = CapturingAuditLogger(log_path=str(tmp_path / "bench_audit.log"))
    orch._audit_logger = audit
    orch.configure_idempotency(enabled=False)
    return orch, audit
