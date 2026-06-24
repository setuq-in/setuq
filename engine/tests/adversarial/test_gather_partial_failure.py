"""Adversarial tests for asyncio.gather partial-failure propagation (Sprint 1).

Sprint 1 fix: orchestrator now uses return_exceptions=True in asyncio.gather
and re-raises the first exception. These tests verify that a component failure
mid-pipeline (planner, SPL generator, summarizer, analysis agent) is never
silently swallowed — it must propagate out of orch.run().

We reuse the _build_orchestrator / MockLLM helpers from test_orchestrator.
"""
import pytest
from app.llm.base import LLMProvider, LLMResponse, LLMUsage

# Import helpers from the sibling test module (conftest.py is intentionally empty).
from tests.pipeline.test_orchestrator import _build_orchestrator, MockLLM


def _mock_usage() -> LLMUsage:
    return LLMUsage(input_tokens=0, output_tokens=0, cost_usd=0.0, model="mock", latency_ms=0)


# ---------------------------------------------------------------------------
# Planner failure (Step 2 — concurrent with SPL generator)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gather_planner_failure_propagates(tmp_path):
    """If the planner LLM raises, orchestrator must propagate the error."""

    class _BrokenPlannerLLM(MockLLM):
        async def generate(self, system_prompt, history, user_prompt) -> LLMResponse:
            if "investigation planner" in system_prompt:
                raise RuntimeError("planner LLM down")
            return await super().generate(system_prompt, history, user_prompt)

    orch = _build_orchestrator(tmp_path, llm=_BrokenPlannerLLM())
    with pytest.raises(RuntimeError, match="planner LLM down"):
        await orch.run("show me revenue by store")


@pytest.mark.asyncio
async def test_gather_planner_failure_does_not_return_partial_result(tmp_path):
    """When the planner fails, run() must raise — not return a partial PipelineResult."""
    from app.pipeline.orchestrator import PipelineResult

    class _BrokenPlannerLLM(MockLLM):
        async def generate(self, system_prompt, history, user_prompt) -> LLMResponse:
            if "investigation planner" in system_prompt:
                raise ValueError("planner parse error")
            return await super().generate(system_prompt, history, user_prompt)

    orch = _build_orchestrator(tmp_path, llm=_BrokenPlannerLLM())
    result = None
    try:
        result = await orch.run("any query")
    except (ValueError, RuntimeError):
        pass
    assert result is None, (
        "run() must not return a PipelineResult when a gather component fails"
    )


# ---------------------------------------------------------------------------
# SPL generator failure (Step 3 — concurrent with planner)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gather_spl_generator_failure_propagates(tmp_path):
    """If the SPL generator LLM raises, orchestrator must propagate the error."""

    class _BrokenSPLLLM(MockLLM):
        async def generate(self, system_prompt, history, user_prompt) -> LLMResponse:
            if "SPL query generator" in system_prompt:
                raise RuntimeError("SPL generator down")
            return await super().generate(system_prompt, history, user_prompt)

    orch = _build_orchestrator(tmp_path, llm=_BrokenSPLLLM())
    with pytest.raises(RuntimeError, match="SPL generator down"):
        await orch.run("show me revenue by store")


# ---------------------------------------------------------------------------
# Summarizer failure (Step 6 — concurrent with analysis agent)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gather_summarizer_failure_propagates(tmp_path):
    """If the summarizer raises, orchestrator must propagate the error."""

    class _BrokenSummarizerLLM(MockLLM):
        async def generate(self, system_prompt, history, user_prompt) -> LLMResponse:
            if "SOC analyst" in system_prompt:
                raise RuntimeError("summarizer down")
            return await super().generate(system_prompt, history, user_prompt)

    orch = _build_orchestrator(tmp_path, llm=_BrokenSummarizerLLM())
    with pytest.raises(RuntimeError, match="summarizer down"):
        await orch.run("show me revenue by store")


# ---------------------------------------------------------------------------
# Analysis agent failure (Step 7 — concurrent with summarizer)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gather_analysis_agent_failure_propagates(tmp_path):
    """If the analysis agent raises, orchestrator must propagate the error."""

    class _BrokenAnalysisLLM(MockLLM):
        async def generate(self, system_prompt, history, user_prompt) -> LLMResponse:
            if "SOC analysis" in system_prompt:
                raise RuntimeError("analysis agent down")
            return await super().generate(system_prompt, history, user_prompt)

    orch = _build_orchestrator(tmp_path, llm=_BrokenAnalysisLLM())
    with pytest.raises(RuntimeError, match="analysis agent down"):
        await orch.run("show me revenue by store")


# ---------------------------------------------------------------------------
# Both concurrent tasks fail — first exception propagates
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gather_both_concurrent_tasks_fail_one_propagates(tmp_path):
    """When both planner and SPL generator fail, exactly one error must propagate."""

    class _BothBrokenLLM(LLMProvider):
        async def generate(self, system_prompt, history, user_prompt) -> LLMResponse:
            if "investigation planner" in system_prompt:
                raise RuntimeError("planner down")
            if "SPL query generator" in system_prompt:
                raise RuntimeError("SPL gen down")
            return LLMResponse(content="[]", usage=_mock_usage())

    orch = _build_orchestrator(tmp_path, llm=_BothBrokenLLM())
    with pytest.raises(RuntimeError):
        await orch.run("any query")


# ---------------------------------------------------------------------------
# Happy path still works (regression guard)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gather_happy_path_still_works(tmp_path):
    """Baseline: an unmodified MockLLM must complete the pipeline successfully."""
    from app.pipeline.orchestrator import PipelineResult

    orch = _build_orchestrator(tmp_path)
    result = await orch.run("show me revenue by store")
    assert isinstance(result, PipelineResult)
    assert result.query == "show me revenue by store"
