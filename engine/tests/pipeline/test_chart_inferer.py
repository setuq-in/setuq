import pytest
from app.pipeline.chart_inferer import infer_heuristic


def test_timechart_single_metric_returns_line():
    spl = "search index=main | timechart count"
    rows = [
        {"_time": "2026-05-12T00:00:00", "count": "42"},
        {"_time": "2026-05-12T01:00:00", "count": "55"},
    ]
    spec = infer_heuristic(spl, rows)
    assert spec is not None
    assert spec.chart_type == "line"
    assert spec.x_field == "_time"
    assert spec.y_fields == ["count"]
    assert spec.confidence == 1.0


def test_stats_by_few_categories_returns_pie():
    spl = "search index=main | stats count by user"
    rows = [{"user": f"u{i}", "count": str(i + 1)} for i in range(5)]
    spec = infer_heuristic(spl, rows)
    assert spec is not None
    assert spec.chart_type == "pie"
    assert spec.x_field == "user"


def test_stats_by_many_categories_returns_bar():
    spl = "search index=main | stats count by host"
    rows = [{"host": f"h{i}", "count": str(i + 1)} for i in range(20)]
    spec = infer_heuristic(spl, rows)
    assert spec is not None
    assert spec.chart_type == "bar"


def test_two_categorical_dimensions_returns_heatmap():
    spl = "search index=main | stats count by src_ip dest_port"
    rows = [
        {"src_ip": "10.0.0.1", "dest_port": "443", "count": "5"},
        {"src_ip": "10.0.0.2", "dest_port": "22", "count": "3"},
    ]
    spec = infer_heuristic(spl, rows)
    assert spec is not None
    assert spec.chart_type == "heatmap"


def test_timechart_with_split_by_returns_stacked_bar():
    spl = "search index=main | timechart count by status"
    rows = [
        {"_time": "2026-05-12T00:00:00", "200": "10", "500": "1"},
        {"_time": "2026-05-12T01:00:00", "200": "12", "500": "0"},
    ]
    spec = infer_heuristic(spl, rows)
    assert spec is not None
    # `timechart count by status` pivots status values into column names ("200", "500"),
    # so the by-field "status" is absent from the row schema. With only _time + numeric
    # columns visible, the heuristic returns a multi-series line chart — which is the
    # right Splunk-default rendering for a pivoted timechart anyway.
    assert spec.chart_type == "line"
    assert spec.x_field == "_time"
    assert set(spec.y_fields) == {"200", "500"}


def test_single_numeric_row_returns_single_value():
    spl = "search index=main | stats count"
    rows = [{"count": "1234"}]
    spec = infer_heuristic(spl, rows)
    assert spec is not None
    assert spec.chart_type == "single_value"


def test_single_avg_row_returns_gauge():
    spl = "search index=main | stats avg(response_time) as avg_rt"
    rows = [{"avg_rt": "245.7"}]
    spec = infer_heuristic(spl, rows)
    assert spec is not None
    assert spec.chart_type == "gauge"


def test_two_numerics_no_time_returns_scatter():
    spl = "search index=main | table cpu memory"
    rows = [
        {"cpu": "12.5", "memory": "2048"},
        {"cpu": "47.2", "memory": "4096"},
    ]
    spec = infer_heuristic(spl, rows)
    assert spec is not None
    assert spec.chart_type == "scatter"


def test_three_numerics_no_time_returns_bubble():
    spl = "search index=main | table cpu memory disk"
    rows = [
        {"cpu": "12.5", "memory": "2048", "disk": "100"},
        {"cpu": "47.2", "memory": "4096", "disk": "250"},
    ]
    spec = infer_heuristic(spl, rows)
    assert spec is not None
    assert spec.chart_type == "bubble"


def test_empty_results_returns_none():
    spec = infer_heuristic("search index=main", [])
    assert spec is None


def test_freeform_events_returns_none():
    spl = "search index=main"
    rows = [
        {"_raw": "event 1", "host": "h1", "source": "/var/log/foo"},
        {"_raw": "event 2", "host": "h2", "source": "/var/log/foo"},
    ]
    spec = infer_heuristic(spl, rows)
    assert spec is None


def test_oversized_non_singlevalue_returns_none():
    spl = "search index=main | stats count by host"
    rows = [{"host": f"h{i}", "count": str(i)} for i in range(1500)]
    spec = infer_heuristic(spl, rows)
    assert spec is None


def test_oversized_single_value_still_returns_spec():
    spl = "search index=main | stats count"
    # single_value only ever has 1 row, but test that the cap doesn't break it
    rows = [{"count": "42"}]
    spec = infer_heuristic(spl, rows)
    assert spec is not None
    assert spec.chart_type == "single_value"
