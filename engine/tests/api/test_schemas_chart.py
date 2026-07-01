import pytest
from pydantic import ValidationError
from app.api.schemas import ChartSpec, ChartOverrideRequest, QueryResponse


def test_chart_spec_minimal():
    spec = ChartSpec(
        chart_type="line",
        x_field="_time",
        y_fields=["count"],
        series_field=None,
        title="Events over time",
        confidence=1.0,
    )
    assert spec.chart_type == "line"
    assert spec.truncated is False
    assert spec.truncation_note is None


def test_chart_spec_with_truncation():
    spec = ChartSpec(
        chart_type="bar",
        x_field="user",
        y_fields=["count"],
        series_field=None,
        title="Top users",
        confidence=0.9,
        truncated=True,
        truncation_note="Showing first 1000 of 1500 rows",
    )
    assert spec.truncated is True


def test_chart_override_request():
    req = ChartOverrideRequest(chart_type="pie")
    assert req.chart_type == "pie"


def test_query_response_includes_chart_spec_and_message_id():
    fields = QueryResponse.model_fields
    assert "chart_spec" in fields
    assert "message_id" in fields
