"""Tests for FallbackProvider chain."""
import pytest
from app.llm.base import LLMProvider, LLMResponse, LLMUsage
from app.llm.fallback import FallbackProvider


def _usage() -> LLMUsage:
    return LLMUsage(input_tokens=0, output_tokens=0, cost_usd=0.0, model="mock", latency_ms=0)


class _SuccessLLM(LLMProvider):
    def __init__(self, text: str = "ok"):
        self.calls = 0
        self._text = text

    async def generate(self, system_prompt, history, user_prompt) -> LLMResponse:
        self.calls += 1
        return LLMResponse(content=self._text, usage=_usage())


class _FailLLM(LLMProvider):
    def __init__(self, exc=None):
        self.calls = 0
        self._exc = exc or RuntimeError("provider down")

    async def generate(self, system_prompt, history, user_prompt) -> LLMResponse:
        self.calls += 1
        raise self._exc


@pytest.mark.asyncio
async def test_primary_success_secondary_not_called():
    primary = _SuccessLLM("primary response")
    secondary = _SuccessLLM("secondary response")
    fp = FallbackProvider([primary, secondary])
    result = await fp.generate("sys", [], "user")
    assert result.content == "primary response"
    assert primary.calls == 1
    assert secondary.calls == 0


@pytest.mark.asyncio
async def test_primary_fail_uses_secondary():
    primary = _FailLLM()
    secondary = _SuccessLLM("fallback response")
    fp = FallbackProvider([primary, secondary])
    result = await fp.generate("sys", [], "user")
    assert result.content == "fallback response"
    assert primary.calls == 1
    assert secondary.calls == 1


@pytest.mark.asyncio
async def test_all_fail_raises():
    fp = FallbackProvider([_FailLLM(), _FailLLM()])
    with pytest.raises(RuntimeError, match="All 2 LLM providers failed"):
        await fp.generate("sys", [], "user")


@pytest.mark.asyncio
async def test_three_providers_tries_in_order():
    p1 = _FailLLM()
    p2 = _FailLLM()
    p3 = _SuccessLLM("third")
    fp = FallbackProvider([p1, p2, p3])
    result = await fp.generate("sys", [], "user")
    assert result.content == "third"
    assert p1.calls == 1
    assert p2.calls == 1
    assert p3.calls == 1


def test_empty_providers_raises():
    with pytest.raises(ValueError):
        FallbackProvider([])
