import pytest
from app.api.schemas import ChartSpec
from app.pipeline.chart_inferer import ChartInferer


class StubLLM:
    def __init__(self, response: str):
        self.response = response
        self.called = False

    async def generate(self, system_prompt: str, history: list, user_prompt: str) -> str:
        self.called = True
        return self.response


@pytest.mark.asyncio
async def test_high_confidence_skips_llm():
    llm = StubLLM("ignored")
    inferer = ChartInferer(llm=llm)
    spec = await inferer.infer(
        spl="search index=main | timechart count",
        rows=[{"_time": "2026-05-12T00:00:00", "count": "42"}],
    )
    assert spec is not None
    assert spec.chart_type == "line"
    assert llm.called is False


@pytest.mark.asyncio
async def test_low_confidence_invokes_llm_and_returns_refined_spec():
    llm_response = """{
        "chart_type": "stacked_bar",
        "x_field": "_time",
        "y_fields": ["count"],
        "series_field": "host",
        "title": "Events by host over time",
        "confidence": 0.95
    }"""
    llm = StubLLM(llm_response)
    inferer = ChartInferer(llm=llm)
    # Three numerics with no time triggers bubble heuristic at confidence 0.7 → fallback fires
    spec = await inferer.infer(
        spl="search index=main | timechart count by host",
        rows=[
            {"cpu": "12.5", "memory": "2048", "disk": "100"},
            {"cpu": "47.2", "memory": "4096", "disk": "250"},
        ],
    )
    assert spec is not None
    assert spec.chart_type == "stacked_bar"
    assert spec.series_field == "host"
    assert llm.called is True


@pytest.mark.asyncio
async def test_llm_bad_json_falls_back_to_heuristic():
    llm = StubLLM("not valid json {{{")
    inferer = ChartInferer(llm=llm)
    spec = await inferer.infer(
        spl="search index=main",
        rows=[
            {"cpu": "12.5", "memory": "2048", "disk": "100"},
            {"cpu": "47.2", "memory": "4096", "disk": "250"},
        ],
    )
    # Heuristic guess (bubble at confidence 0.7) returned even though LLM failed
    assert spec is not None
    assert spec.chart_type == "bubble"


@pytest.mark.asyncio
async def test_no_heuristic_match_no_llm_call():
    llm = StubLLM("ignored")
    inferer = ChartInferer(llm=llm)
    spec = await inferer.infer(spl="search index=main", rows=[])
    assert spec is None
    assert llm.called is False
