"""Sprint 6 — Audit log carries total_tokens + total_cost_usd from run budget."""
import json
import pytest
from app.llm.base import LLMProvider, LLMResponse, LLMUsage
from app.llm.harness import HarnessedProvider
from tests.pipeline.test_orchestrator import _build_orchestrator


def _u() -> LLMUsage:
    return LLMUsage(input_tokens=100, output_tokens=50, cost_usd=0.001, model="mock", latency_ms=1)


class _CostedMockLLM(LLMProvider):
    """Same routing as MockLLM but emits non-zero usage so audit cost is verifiable."""
    async def generate(self, system_prompt, history, user_prompt) -> LLMResponse:
        if "SPL query generator" in system_prompt:
            return LLMResponse(
                content='index=main earliest=-1d | stats count',
                usage=_u(),
            )
        if "Explain" in system_prompt:
            return LLMResponse(content="counts events", usage=_u())
        if "SOC analyst" in system_prompt:
            return LLMResponse(content='[]', usage=_u())
        if "investigation planner" in system_prompt:
            return LLMResponse(
                content='{"needs_plan": false, "steps": [{"description": "x", "spl_hint": null}], "reasoning": "y"}',
                usage=_u(),
            )
        if "SOC analysis" in system_prompt:
            return LLMResponse(
                content='{"anomalies": [], "patterns": [], "summary": "none"}',
                usage=_u(),
            )
        if "decision engine" in system_prompt:
            return LLMResponse(
                content='{"confidence_score": 0.5, "risk_level": "low", "reasoning": "ok", "recommendation": "suggest", "priority_actions": []}',
                usage=_u(),
            )
        return LLMResponse(content="summary", usage=_u())


@pytest.mark.asyncio
async def test_audit_log_records_total_tokens_and_cost(tmp_path):
    audit_path = tmp_path / "audit.log"
    import app.pipeline.audit_logger as al
    al._instance = None
    al.init_audit_logger(str(audit_path))

    harnessed = HarnessedProvider(base=_CostedMockLLM(), timeout_seconds=5.0, max_retries=1)
    orch = _build_orchestrator(tmp_path, llm=harnessed, guardrail_indexes=["main"])
    await orch.run("show counts", session_id="sess-cost")

    al._instance._listener.stop()
    al._instance = None

    lines = audit_path.read_text().splitlines()
    assert lines, "audit log empty"
    entry = json.loads(lines[-1])
    assert entry["total_tokens"] > 0, f"expected nonzero tokens, got {entry['total_tokens']}"
    assert entry["total_cost_usd"] > 0.0, f"expected nonzero cost, got {entry['total_cost_usd']}"
