import time
import pytest
from bench.mocks import LatencyMockLLM, FaultyMockLLM


@pytest.mark.asyncio
async def test_latency_mock_sleeps():
    llm = LatencyMockLLM(delay_ms=50)
    t0 = time.monotonic()
    resp = await llm.generate("SPL query generator", [], "q")
    assert (time.monotonic() - t0) >= 0.05
    assert "index=" in resp.content


@pytest.mark.asyncio
async def test_faulty_mock_error_raises():
    llm = FaultyMockLLM(mode="error")
    with pytest.raises(RuntimeError, match="injected LLM failure"):
        await llm.generate("any", [], "q")
