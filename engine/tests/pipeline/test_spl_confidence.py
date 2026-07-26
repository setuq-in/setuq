import pytest
from app.llm.base import LLMProvider, LLMResponse, LLMUsage
from app.pipeline.spl_confidence import SPLConfidenceScorer


def _usage():
    return LLMUsage(input_tokens=1, output_tokens=1, cost_usd=0.0, model="mock", latency_ms=0)


class _FixedLLM(LLMProvider):
    def __init__(self, content):
        self._content = content

    async def generate(self, system_prompt, history, user_prompt):
        return LLMResponse(content=self._content, usage=_usage())


@pytest.mark.asyncio
async def test_score_returns_float():
    scorer = SPLConfidenceScorer(llm=_FixedLLM('{"confidence": 0.82, "reasoning": "ok"}'))
    score = await scorer.score(query="q", spl="index=main earliest=-1d", schema_context="")
    assert score == pytest.approx(0.82)


@pytest.mark.asyncio
async def test_score_clamps_out_of_range():
    scorer = SPLConfidenceScorer(llm=_FixedLLM('{"confidence": 1.7, "reasoning": "x"}'))
    assert await scorer.score(query="q", spl="s", schema_context="") == 1.0
    scorer_low = SPLConfidenceScorer(llm=_FixedLLM('{"confidence": -0.5, "reasoning": "x"}'))
    assert await scorer_low.score(query="q", spl="s", schema_context="") == 0.0


@pytest.mark.asyncio
async def test_score_returns_none_on_invalid_output():
    # Non-JSON, unrecoverable output -> generate_validated raises
    # LLMOutputValidationError after its retry -> scorer returns None.
    scorer = SPLConfidenceScorer(llm=_FixedLLM('not json at all'))
    assert await scorer.score(query="q", spl="s", schema_context="") is None
