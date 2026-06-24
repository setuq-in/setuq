"""Sprint 6 — Retry policy boundaries: retry 429/5xx/conn-errs; give up on 4xx-non-429."""
import pytest
import httpx
from app.llm.base import LLMProvider, LLMResponse, LLMUsage
from app.llm.harness import HarnessedProvider


def _usage() -> LLMUsage:
    return LLMUsage(input_tokens=0, output_tokens=0, cost_usd=0.0, model="stub", latency_ms=0)


def _http(code: int) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://api.test/x")
    return httpx.HTTPStatusError("x", request=req, response=httpx.Response(code, request=req))


class _CountingLLM(LLMProvider):
    def __init__(self, exc_factory):
        self.calls = 0
        self._exc = exc_factory

    async def generate(self, system_prompt, history, user_prompt) -> LLMResponse:
        self.calls += 1
        raise self._exc()


@pytest.mark.parametrize("code", [400, 404, 422])
@pytest.mark.asyncio
async def test_no_retry_on_4xx_non_429(code):
    base = _CountingLLM(lambda: _http(code))
    p = HarnessedProvider(base=base, timeout_seconds=1.0, max_retries=3)
    with pytest.raises(httpx.HTTPStatusError):
        await p.generate("s", [], "u")
    assert base.calls == 1, f"4xx-{code} should not retry; got {base.calls} calls"


@pytest.mark.asyncio
async def test_retry_on_429():
    base = _CountingLLM(lambda: _http(429))
    p = HarnessedProvider(base=base, timeout_seconds=1.0, max_retries=3)
    with pytest.raises(httpx.HTTPStatusError):
        await p.generate("s", [], "u")
    assert base.calls == 3, "429 should consume all retries"


@pytest.mark.parametrize("code", [500, 502, 503])
@pytest.mark.asyncio
async def test_retry_on_5xx(code):
    base = _CountingLLM(lambda: _http(code))
    p = HarnessedProvider(base=base, timeout_seconds=1.0, max_retries=3)
    with pytest.raises(httpx.HTTPStatusError):
        await p.generate("s", [], "u")
    assert base.calls == 3, f"5xx-{code} should consume all retries"


@pytest.mark.asyncio
async def test_retry_on_connect_error():
    base = _CountingLLM(lambda: httpx.ConnectError("down"))
    p = HarnessedProvider(base=base, timeout_seconds=1.0, max_retries=3)
    with pytest.raises(httpx.ConnectError):
        await p.generate("s", [], "u")
    assert base.calls == 3
