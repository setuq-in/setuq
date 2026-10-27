import pytest
from bench.resilience_bench import run_resilience_bench, FaultOutcome


@pytest.mark.asyncio
async def test_resilience_matrix_covers_faults(tmp_path):
    outcomes = await run_resilience_bench(tmp_path)
    faults = {o.fault for o in outcomes}
    assert {"llm_error", "guardrail_block", "splunk_down"} <= faults
    # guardrail block must be a clean, served-as-rejected outcome
    gb = next(o for o in outcomes if o.fault == "guardrail_block")
    assert gb.error is not None            # raised GuardrailViolation, did not hang
    assert "delete" in gb.error.lower()    # actually a guardrail rejection, not some other failure
