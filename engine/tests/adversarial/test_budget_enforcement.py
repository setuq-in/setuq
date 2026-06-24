"""Adversarial tests for HarnessedProvider budget enforcement (Sprint 1).

Verifies:
- Token budget raises BudgetExceeded when cumulative tokens exceed the cap
- Cost budget raises BudgetExceeded when cumulative cost exceeds the cap
- No budget is enforced when init_run_budget() was never called (opt-in)
- reset_run_budget() truly resets the accumulator to zero
- A provider error does not phantom-charge the budget
- BudgetExceeded is never retried (it re-raises immediately)
"""
import asyncio
import pytest
from app.llm.harness import (
    HarnessedProvider,
    BudgetExceeded,
    init_run_budget,
    reset_run_budget,
    _run_budget,
)
from app.llm.base import LLMProvider, LLMResponse, LLMUsage


# ---------------------------------------------------------------------------
# Stub providers
# ---------------------------------------------------------------------------

class _HighCostLLM(LLMProvider):
    """Returns 20 000 tokens (15k in + 5k out) and $0.20 per call."""

    async def generate(self, system_prompt, history, user_prompt) -> LLMResponse:
        return LLMResponse(
            content="response",
            usage=LLMUsage(
                input_tokens=15_000,
                output_tokens=5_000,
                cost_usd=0.20,
                model="stub",
                latency_ms=10,
            ),
        )


class _CheapLLM(LLMProvider):
    """Returns 20 tokens and $0.0001 per call."""

    async def generate(self, system_prompt, history, user_prompt) -> LLMResponse:
        return LLMResponse(
            content="ok",
            usage=LLMUsage(
                input_tokens=10,
                output_tokens=10,
                cost_usd=0.0001,
                model="stub",
                latency_ms=1,
            ),
        )


class _FailLLM(LLMProvider):
    """Always raises RuntimeError."""

    async def generate(self, *args, **kwargs):
        raise RuntimeError("provider down")


# ---------------------------------------------------------------------------
# Token budget
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_budget_exceeded_on_token_overrun():
    """Second call must raise BudgetExceeded when cumulative tokens exceed the cap."""
    provider = HarnessedProvider(base=_HighCostLLM(), timeout_seconds=5.0, max_retries=1)
    token = init_run_budget(max_tokens=30_000, max_cost_usd=100.0)
    try:
        # Call 1: 20 000 tokens accumulated — still under 30 000
        await provider.generate("sys", [], "user")
        # Call 2: 40 000 total — exceeds 30 000
        with pytest.raises(BudgetExceeded, match="Token budget"):
            await provider.generate("sys", [], "user")
    finally:
        reset_run_budget(token)


@pytest.mark.asyncio
async def test_budget_first_call_within_limit_passes():
    """The first call (20 000 tokens) under a 25 000-token cap must succeed."""
    provider = HarnessedProvider(base=_HighCostLLM(), timeout_seconds=5.0, max_retries=1)
    token = init_run_budget(max_tokens=25_000, max_cost_usd=100.0)
    try:
        result = await provider.generate("sys", [], "user")
        assert result.content == "response"
    finally:
        reset_run_budget(token)


@pytest.mark.asyncio
async def test_budget_exceeded_immediately_when_single_call_over_cap():
    """If max_tokens < tokens in one call, must raise on first call."""
    provider = HarnessedProvider(base=_HighCostLLM(), timeout_seconds=5.0, max_retries=1)
    token = init_run_budget(max_tokens=1_000, max_cost_usd=100.0)
    try:
        with pytest.raises(BudgetExceeded, match="Token budget"):
            await provider.generate("sys", [], "user")
    finally:
        reset_run_budget(token)


# ---------------------------------------------------------------------------
# Cost budget
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_budget_exceeded_on_cost_overrun():
    """Second call must raise BudgetExceeded when cumulative cost exceeds the cap."""
    provider = HarnessedProvider(base=_HighCostLLM(), timeout_seconds=5.0, max_retries=1)
    token = init_run_budget(max_tokens=1_000_000, max_cost_usd=0.30)
    try:
        # Call 1: $0.20 — under $0.30
        await provider.generate("sys", [], "user")
        # Call 2: $0.40 total — exceeds $0.30
        with pytest.raises(BudgetExceeded, match="Cost budget"):
            await provider.generate("sys", [], "user")
    finally:
        reset_run_budget(token)


@pytest.mark.asyncio
async def test_budget_exceeded_immediately_when_single_call_over_cost_cap():
    """If max_cost_usd < cost of one call, must raise on first call."""
    provider = HarnessedProvider(base=_HighCostLLM(), timeout_seconds=5.0, max_retries=1)
    token = init_run_budget(max_tokens=1_000_000, max_cost_usd=0.01)
    try:
        with pytest.raises(BudgetExceeded, match="Cost budget"):
            await provider.generate("sys", [], "user")
    finally:
        reset_run_budget(token)


# ---------------------------------------------------------------------------
# Opt-in: no budget without init
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_budget_not_enforced_without_init():
    """Without init_run_budget(), any number of calls must succeed (budget is opt-in)."""
    provider = HarnessedProvider(base=_HighCostLLM(), timeout_seconds=5.0, max_retries=1)
    # Make two expensive calls — neither should raise
    result1 = await provider.generate("sys", [], "user")
    result2 = await provider.generate("sys", [], "user")
    assert result1.content == "response"
    assert result2.content == "response"


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_budget_reset_clears_accumulator():
    """After reset_run_budget(), a new budget window starts from zero."""
    provider = HarnessedProvider(base=_HighCostLLM(), timeout_seconds=5.0, max_retries=1)

    # Window 1: consume 20 000 tokens (under 25 000 cap)
    token1 = init_run_budget(max_tokens=25_000, max_cost_usd=100.0)
    try:
        await provider.generate("sys", [], "user")
    finally:
        reset_run_budget(token1)

    # Window 2: fresh 25 000-token cap — first call must succeed again
    token2 = init_run_budget(max_tokens=25_000, max_cost_usd=100.0)
    try:
        result = await provider.generate("sys", [], "user")
        assert result.content == "response"
    finally:
        reset_run_budget(token2)


@pytest.mark.asyncio
async def test_budget_reset_restores_none():
    """After reset, _run_budget context var must be None (no budget active)."""
    token = init_run_budget(max_tokens=1_000, max_cost_usd=1.0)
    reset_run_budget(token)
    assert _run_budget.get() is None


# ---------------------------------------------------------------------------
# Provider error must not phantom-charge the budget
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_budget_not_consumed_on_provider_error():
    """If the provider raises before returning usage, the budget accumulator must stay at zero."""
    provider = HarnessedProvider(base=_FailLLM(), timeout_seconds=5.0, max_retries=1)
    token = init_run_budget(max_tokens=100, max_cost_usd=100.0)
    try:
        with pytest.raises(RuntimeError, match="provider down"):
            await provider.generate("sys", [], "user")
        # Budget must still be at zero — no phantom charge
        budget = _run_budget.get()
        assert budget is not None
        assert budget.used_tokens == 0, (
            f"Budget was charged {budget.used_tokens} tokens despite provider error"
        )
        assert budget.used_cost_usd == 0.0, (
            f"Budget was charged ${budget.used_cost_usd} despite provider error"
        )
    finally:
        reset_run_budget(token)


# ---------------------------------------------------------------------------
# BudgetExceeded is not retried
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_budget_exceeded_not_retried():
    """BudgetExceeded must propagate immediately without retry attempts."""
    call_count = 0

    class _CountingLLM(LLMProvider):
        async def generate(self, *args, **kwargs) -> LLMResponse:
            nonlocal call_count
            call_count += 1
            return LLMResponse(
                content="x",
                usage=LLMUsage(
                    input_tokens=15_000,
                    output_tokens=5_000,
                    cost_usd=0.20,
                    model="stub",
                    latency_ms=1,
                ),
            )

    # max_retries=3 to make sure retries would happen for other errors
    provider = HarnessedProvider(base=_CountingLLM(), timeout_seconds=5.0, max_retries=3)
    token = init_run_budget(max_tokens=1_000, max_cost_usd=100.0)
    try:
        with pytest.raises(BudgetExceeded):
            await provider.generate("sys", [], "user")
        # Only 1 attempt — no retries for budget errors
        assert call_count == 1, (
            f"BudgetExceeded triggered {call_count} provider calls — retries should not happen"
        )
    finally:
        reset_run_budget(token)


# ---------------------------------------------------------------------------
# Cheap provider stays under budget across many calls
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cheap_calls_do_not_falsely_trigger_budget():
    """Many cheap calls that stay well under the cap must all succeed."""
    provider = HarnessedProvider(base=_CheapLLM(), timeout_seconds=5.0, max_retries=1)
    token = init_run_budget(max_tokens=10_000, max_cost_usd=1.0)
    try:
        for _ in range(10):
            result = await provider.generate("sys", [], "user")
            assert result.content == "ok"
    finally:
        reset_run_budget(token)
