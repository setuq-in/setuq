import pytest
from bench.latency_bench import run_latency_bench


@pytest.mark.asyncio
async def test_latency_bench_produces_stage_summaries(tmp_path):
    payload = await run_latency_bench(reps=3, warmup=1, delay_ms=1, tmp_path=tmp_path)
    assert "stages" in payload
    # guardrail stage always runs for a valid query
    assert "pipeline.guardrail" in payload["stages"]
    assert payload["stages"]["pipeline.guardrail"]["count"] >= 1
    assert "e2e" in payload
    assert payload["cost_usd_mean"] == 0.0
    assert payload["runs_ok"] >= 1
