import pytest
from app.pipeline.chart_inferer import ChartInferer
from tests.pipeline.test_orchestrator import _build_orchestrator, MockLLM


@pytest.mark.asyncio
async def test_chart_spec_attached_to_result(tmp_path):
    inferer = ChartInferer(llm=MockLLM())
    orch = _build_orchestrator(tmp_path, chart_inferer=inferer)
    result = await orch.run("revenue by store")
    # Two rows: 1 categorical (store_id) + 1 numeric -> pie (heuristic conf 0.9)
    assert result.chart_spec is not None
    assert result.chart_spec.chart_type == "pie"


@pytest.mark.asyncio
async def test_no_chart_inferer_yields_none(tmp_path):
    orch = _build_orchestrator(tmp_path)
    result = await orch.run("revenue by store")
    assert result.chart_spec is None


@pytest.mark.asyncio
async def test_chart_inference_failure_does_not_break_response(tmp_path):
    class Boom(ChartInferer):
        async def infer(self, spl, rows):
            raise RuntimeError("inference exploded")

    orch = _build_orchestrator(tmp_path, chart_inferer=Boom(llm=MockLLM()))
    result = await orch.run("revenue by store")
    assert result.chart_spec is None
    assert result.summary  # main pipeline still produced output
