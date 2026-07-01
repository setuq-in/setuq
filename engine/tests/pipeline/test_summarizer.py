import pytest
from app.pipeline.summarizer import Summarizer
from app.llm.base import LLMProvider, LLMResponse, LLMUsage


def _mock_usage() -> LLMUsage:
    return LLMUsage(input_tokens=0, output_tokens=0, cost_usd=0.0, model="mock", latency_ms=0)


class MockLLM(LLMProvider):
    def __init__(self, response: str):
        self._response = response

    async def generate(self, system_prompt: str, history: list[dict], user_prompt: str) -> LLMResponse:
        return LLMResponse(content=self._response, usage=_mock_usage())


@pytest.mark.asyncio
async def test_summarize_returns_llm_response():
    mock_llm = MockLLM(response="Store S093 had the highest revenue at $61.33.")
    summarizer = Summarizer(llm=mock_llm)
    summary = await summarizer.summarize(
        query="Which store had the most revenue?",
        spl="index=chocolate_index sourcetype=sales | stats sum(revenue) by store_id",
        results=[{"store_id": "S093", "sum(revenue)": "61.33"}],
    )
    assert summary == "Store S093 had the highest revenue at $61.33."


@pytest.mark.asyncio
async def test_summarize_prompt_includes_context():
    captured_prompts = {}

    class CaptureLLM(LLMProvider):
        async def generate(self, system_prompt: str, history: list[dict], user_prompt: str) -> LLMResponse:
            captured_prompts["system"] = system_prompt
            captured_prompts["user"] = user_prompt
            return LLMResponse(content="Summary here", usage=_mock_usage())

    summarizer = Summarizer(llm=CaptureLLM())
    await summarizer.summarize(
        query="Total profit",
        spl="index=chocolate_index sourcetype=sales | stats sum(profit)",
        results=[{"sum(profit)": "1000"}],
    )
    assert "Total profit" in captured_prompts["user"]
    assert "sum(profit)" in captured_prompts["user"]


@pytest.mark.asyncio
async def test_summarize_handles_empty_results():
    mock_llm = MockLLM(response="No results were found for your query.")
    summarizer = Summarizer(llm=mock_llm)
    summary = await summarizer.summarize(
        query="Find orders from 2025",
        spl="index=chocolate_index sourcetype=sales | search order_date=2025*",
        results=[],
    )
    assert "No results" in summary


@pytest.mark.asyncio
async def test_summarize_samples_large_results_with_sketch():
    """Large result sets must be stratified-sampled (k=60) with a sketch line in prompt."""
    captured = {}

    class CaptureLLM(LLMProvider):
        async def generate(self, system_prompt: str, history: list[dict], user_prompt: str) -> LLMResponse:
            captured["user"] = user_prompt
            return LLMResponse(content="Summary", usage=_mock_usage())

    summarizer = Summarizer(llm=CaptureLLM())
    big_results = [{"id": str(i)} for i in range(200)]
    await summarizer.summarize(
        query="test", spl="index=test | stats count", results=big_results,
    )
    assert '"0"' in captured["user"], "First row missing from sample"
    assert '"199"' in captured["user"], "Last row missing from sample"
    assert "200 total rows" in captured["user"]


@pytest.mark.asyncio
async def test_summarize_passes_history():
    """History should be forwarded to LLM."""
    captured = {}

    class CaptureLLM(LLMProvider):
        async def generate(self, system_prompt: str, history: list[dict], user_prompt: str) -> LLMResponse:
            captured["history"] = history
            return LLMResponse(content="Summary", usage=_mock_usage())

    summarizer = Summarizer(llm=CaptureLLM())
    test_history = [{"role": "user", "content": "prior"}, {"role": "assistant", "content": "response"}]
    await summarizer.summarize(
        query="test", spl="index=test", results=[], history=test_history,
    )
    assert captured["history"] == test_history


@pytest.mark.asyncio
async def test_summarize_none_history_defaults_to_empty():
    """Explicit None history should become empty list."""
    captured = {}

    class CaptureLLM(LLMProvider):
        async def generate(self, system_prompt: str, history: list[dict], user_prompt: str) -> LLMResponse:
            captured["history"] = history
            return LLMResponse(content="Summary", usage=_mock_usage())

    summarizer = Summarizer(llm=CaptureLLM())
    await summarizer.summarize(
        query="test", spl="index=test", results=[], history=None,
    )
    assert captured["history"] == []
