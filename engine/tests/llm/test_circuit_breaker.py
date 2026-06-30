"""Sprint 6 — Auth/quota circuit breaker on HarnessedProvider."""
import pytest
import httpx
from app.llm.base import LLMProvider, LLMResponse, LLMUsage
from app.llm.harness import HarnessedProvider, CircuitOpen


def _usage() -> LLMUsage:
    return LLMUsage(input_tokens=0, output_tokens=0, cost_usd=0.0, model="stub", latency_ms=0)


def _auth_error() -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://api.test/x")
    resp = httpx.Response(401, request=req)
    return httpx.HTTPStatusError("unauthorized", request=req, response=resp)


def _server_error() -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://api.test/x")
    resp = httpx.Response(503, request=req)
    return httpx.HTTPStatusError("unavailable", request=req, response=resp)


class _AuthFailLLM(LLMProvider):
    def __init__(self):
        self.calls = 0

    async def generate(self, system_prompt, history, user_prompt) -> LLMResponse:
        self.calls += 1
        raise _auth_error()


class _ServerFailLLM(LLMProvider):
    def __init__(self):
        self.calls = 0

    async def generate(self, system_prompt, history, user_prompt) -> LLMResponse:
        self.calls += 1
        raise _server_error()


class _OKLLM(LLMProvider):
    def __init__(self):
        self.calls = 0

    async def generate(self, system_prompt, history, user_prompt) -> LLMResponse:
        self.calls += 1
        return LLMResponse(content="ok", usage=_usage())


@pytest.mark.asyncio
async def test_circuit_opens_after_threshold_auth_failures():
    base = _AuthFailLLM()
    p = HarnessedProvider(base=base, timeout_seconds=1.0, max_retries=1, auth_failure_threshold=3)
    for _ in range(3):
        with pytest.raises(httpx.HTTPStatusError):
            await p.generate("s", [], "u")
    # Circuit now open — next call must fail fast without invoking base
    base_calls_before = base.calls
    with pytest.raises(CircuitOpen):
        await p.generate("s", [], "u")
    assert base.calls == base_calls_before, "Base provider invoked despite open circuit"


@pytest.mark.asyncio
async def test_circuit_stays_closed_on_5xx_only():
    base = _ServerFailLLM()
    p = HarnessedProvider(base=base, timeout_seconds=1.0, max_retries=1, auth_failure_threshold=3)
    for _ in range(5):
        with pytest.raises(httpx.HTTPStatusError):
            await p.generate("s", [], "u")
    # 5xx is transient — circuit must NOT open
    assert not p._circuit.open


@pytest.mark.asyncio
async def test_circuit_half_opens_after_ttl_and_closes_on_success():
    """After the circuit opens, it must allow one probe after open_seconds elapses.
    A successful probe must close the circuit permanently."""
    import time
    base = _AuthFailLLM()
    p = HarnessedProvider(base=base, timeout_seconds=1.0, max_retries=1,
                          auth_failure_threshold=3)
    for _ in range(3):
        with pytest.raises(httpx.HTTPStatusError):
            await p.generate("s", [], "u")

    # Circuit open — probe must be blocked
    with pytest.raises(CircuitOpen):
        await p.generate("s", [], "u")

    # Simulate TTL expiry by backdating opened_at
    p._circuit.opened_at = time.monotonic() - (p._circuit.open_seconds + 1)

    # Half-open probe: base provider is still failing, so probe fails and circuit re-opens
    with pytest.raises(httpx.HTTPStatusError):
        await p.generate("s", [], "u")

    # Circuit should be open again (probe failed — re-latched)
    with pytest.raises(CircuitOpen):
        await p.generate("s", [], "u")


@pytest.mark.asyncio
async def test_circuit_half_open_closes_on_successful_probe():
    """A successful probe in half-open state must permanently close the circuit."""
    import time

    class _RecoveringLLM(LLMProvider):
        def __init__(self, fail_times):
            self.calls = 0
            self._fail_times = fail_times

        async def generate(self, system_prompt, history, user_prompt) -> LLMResponse:
            self.calls += 1
            if self.calls <= self._fail_times:
                raise _auth_error()
            return LLMResponse(content="ok", usage=_usage())

    base = _RecoveringLLM(fail_times=3)
    p = HarnessedProvider(base=base, timeout_seconds=1.0, max_retries=1,
                          auth_failure_threshold=3)
    for _ in range(3):
        with pytest.raises(httpx.HTTPStatusError):
            await p.generate("s", [], "u")

    # Backdate opened_at to simulate TTL expiry
    p._circuit.opened_at = time.monotonic() - (p._circuit.open_seconds + 1)

    # Half-open probe succeeds → circuit closes
    result = await p.generate("s", [], "u")
    assert result.content == "ok"
    assert p._circuit.opened_at is None, "Circuit must be closed after successful probe"
    assert p._circuit.consecutive_failures == 0


@pytest.mark.asyncio
async def test_circuit_resets_on_success():
    class _FlakyLLM(LLMProvider):
        def __init__(self):
            self.calls = 0

        async def generate(self, system_prompt, history, user_prompt) -> LLMResponse:
            self.calls += 1
            if self.calls <= 2:
                raise _auth_error()
            return LLMResponse(content="ok", usage=_usage())

    base = _FlakyLLM()
    p = HarnessedProvider(base=base, timeout_seconds=1.0, max_retries=1, auth_failure_threshold=5)
    for _ in range(2):
        with pytest.raises(httpx.HTTPStatusError):
            await p.generate("s", [], "u")
    assert p._circuit.consecutive_failures == 2
    # Success resets counter
    result = await p.generate("s", [], "u")
    assert result.content == "ok"
    assert p._circuit.consecutive_failures == 0
    assert not p._circuit.open
