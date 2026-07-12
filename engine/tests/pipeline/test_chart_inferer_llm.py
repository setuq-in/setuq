import pytest
from app.api.schemas import ChartSpec
from app.pipeline.chart_inferer import (
    ChartInferer, detect_requested_chart_type, detect_requested_chart_types,
)


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
        query="chart the count over time",
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
        query="chart events by host",
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
        query="plot cpu vs memory",
    )
    # Heuristic guess (bubble at confidence 0.7) returned even though LLM failed
    assert spec is not None
    assert spec.chart_type == "bubble"


@pytest.mark.asyncio
async def test_no_chart_request_returns_none():
    """Chartable data but no chart words in the query → no chart (opt-in)."""
    llm = StubLLM("ignored")
    inferer = ChartInferer(llm=llm)
    spec = await inferer.infer(
        spl="search index=main | timechart count",
        rows=[{"_time": "2026-05-12T00:00:00", "count": "42"}],
        query="how many events over time",
    )
    assert spec is None
    assert llm.called is False


@pytest.mark.asyncio
async def test_no_query_returns_none():
    """No query at all → no chart."""
    llm = StubLLM("ignored")
    inferer = ChartInferer(llm=llm)
    spec = await inferer.infer(
        spl="search index=main | timechart count",
        rows=[{"_time": "2026-05-12T00:00:00", "count": "42"}],
    )
    assert spec is None


@pytest.mark.asyncio
async def test_no_heuristic_match_no_llm_call():
    llm = StubLLM("ignored")
    inferer = ChartInferer(llm=llm)
    spec = await inferer.infer(spl="search index=main", rows=[])
    assert spec is None
    assert llm.called is False


@pytest.mark.parametrize(
    "query,expected",
    [
        ("Can you create a pie chart by products?", "pie"),
        ("show me a donut of sales", "pie"),
        ("stacked bar of events by host", "stacked_bar"),
        ("bar chart of logins", "bar"),
        ("plot the trend as a line", "line"),
        ("heat map of ports", "heatmap"),
        ("top 10 products by revenue", None),
        ("", None),
        (None, None),
    ],
)
def test_detect_requested_chart_type(query, expected):
    assert detect_requested_chart_type(query) == expected


@pytest.mark.parametrize(
    "query,expected",
    [
        ("pie and bar of logins", ["pie", "bar"]),
        ("show pie, line, and bar", ["pie", "line", "bar"]),
        ("stacked bar chart of events", ["stacked_bar"]),   # not [stacked_bar, bar]
        ("bar & column by host", ["bar", "column"]),
        ("pie chart of products", ["pie"]),
        ("just a chart please", []),                          # generic, no type
        ("top 10 products", []),
        (None, []),
    ],
)
def test_detect_requested_chart_types(query, expected):
    assert detect_requested_chart_types(query) == expected


@pytest.mark.asyncio
async def test_infer_all_multiple_types():
    llm = StubLLM("ignored")
    inferer = ChartInferer(llm=llm)
    rows = [{"product_id": f"p{i}", "revenue": str(i + 1)} for i in range(5)]
    specs = await inferer.infer_all(
        spl="search index=main | stats sum(revenue) as revenue by product_id",
        rows=rows,
        query="show a pie and a bar of revenue by product",
    )
    assert [s.chart_type for s in specs] == ["pie", "bar"]
    assert all(s.requested_by_user for s in specs)
    assert all(s.x_field == "product_id" for s in specs)
    assert llm.called is False


@pytest.mark.asyncio
async def test_infer_all_generic_single():
    llm = StubLLM("ignored")
    inferer = ChartInferer(llm=llm)
    specs = await inferer.infer_all(
        spl="search index=main | timechart count",
        rows=[{"_time": "2026-05-12T00:00:00", "count": "42"}],
        query="chart the count",
    )
    assert len(specs) == 1
    assert specs[0].chart_type == "line"


@pytest.mark.asyncio
async def test_infer_all_no_request_empty():
    llm = StubLLM("ignored")
    inferer = ChartInferer(llm=llm)
    specs = await inferer.infer_all(
        spl="search index=main | timechart count",
        rows=[{"_time": "2026-05-12T00:00:00", "count": "42"}],
        query="how many events",
    )
    assert specs == []


@pytest.mark.asyncio
async def test_explicit_pie_request_overrides_bar_heuristic():
    """10 categories would heuristically be a bar; an explicit 'pie' wins."""
    llm = StubLLM("ignored")
    inferer = ChartInferer(llm=llm)
    rows = [{"product_id": f"p{i}", "revenue": str(i + 1)} for i in range(10)]
    spec = await inferer.infer(
        spl="search index=chocolate_index sourcetype=sales earliest=-90d "
        "| stats sum(revenue) as revenue by product_id | sort -revenue | head 10",
        rows=rows,
        query="Can you create a pie chart by products?",
    )
    assert spec is not None
    assert spec.chart_type == "pie"
    assert spec.x_field == "product_id"
    assert spec.y_fields == ["revenue"]
    assert spec.requested_by_user is True
    assert llm.called is False
