import pytest
from app.pipeline.chart_inferer import ChartInferer
from tests.pipeline.test_orchestrator import _build_orchestrator, MockLLM


@pytest.mark.asyncio
async def test_chart_spec_attached_to_result(tmp_path):
    inferer = ChartInferer(llm=MockLLM())
    orch = _build_orchestrator(tmp_path, chart_inferer=inferer)
    # Chart is opt-in — the query must ask for one.
    result = await orch.run("chart revenue by store")
    # Two rows: 1 categorical (store_id) + 1 numeric -> pie (heuristic conf 0.9)
    assert result.chart_spec is not None
    assert result.chart_spec.chart_type == "pie"


@pytest.mark.asyncio
async def test_no_chart_when_not_requested(tmp_path):
    inferer = ChartInferer(llm=MockLLM())
    orch = _build_orchestrator(tmp_path, chart_inferer=inferer)
    result = await orch.run("revenue by store")  # no chart words
    assert result.chart_spec is None


@pytest.mark.asyncio
async def test_rechart_uses_cached_results(tmp_path):
    inferer = ChartInferer(llm=MockLLM())
    orch = _build_orchestrator(tmp_path, chart_inferer=inferer)
    # First a plain query (no chart) — rows get cached.
    result = await orch.run("revenue by store")
    assert result.chart_spec is None

    # Now re-chart the same results without a new query.
    specs = await orch.rechart(result.session_id, chart_type="pie and bar")
    assert [s.chart_type for s in specs] == ["pie", "bar"]
    assert all(s.requested_by_user for s in specs)


@pytest.mark.asyncio
async def test_rechart_miss_returns_empty(tmp_path):
    inferer = ChartInferer(llm=MockLLM())
    orch = _build_orchestrator(tmp_path, chart_inferer=inferer)
    assert await orch.rechart("unknown-session", chart_type="pie") == []


@pytest.mark.asyncio
async def test_no_chart_inferer_yields_none(tmp_path):
    orch = _build_orchestrator(tmp_path)
    result = await orch.run("revenue by store")
    assert result.chart_spec is None


@pytest.mark.asyncio
async def test_chart_inference_failure_does_not_break_response(tmp_path):
    class Boom(ChartInferer):
        async def infer(self, spl, rows, query=None):
            raise RuntimeError("inference exploded")

    orch = _build_orchestrator(tmp_path, chart_inferer=Boom(llm=MockLLM()))
    result = await orch.run("revenue by store")
    assert result.chart_spec is None
    assert result.summary  # main pipeline still produced output
