import pytest
from bench.factory import build_bench_orchestrator
from bench.mocks import LatencyMockLLM


@pytest.mark.asyncio
async def test_factory_runs_and_captures_cost(tmp_path):
    orch, audit = build_bench_orchestrator(tmp_path, llm=LatencyMockLLM(delay_ms=1))
    result = await orch.run("Show me total revenue by store", session_id="bench-1")
    assert result.spl
    assert audit.last_cost() == 0.0          # MockLLM cost is zero
    assert len(audit.entries) == 1


@pytest.mark.asyncio
async def test_factory_idempotency_disabled(tmp_path):
    orch, audit = build_bench_orchestrator(tmp_path, llm=LatencyMockLLM(delay_ms=1))
    await orch.run("same query", session_id="bench-2")
    await orch.run("same query", session_id="bench-2")
    assert len(audit.entries) == 2           # both runs executed, no cache short-circuit
