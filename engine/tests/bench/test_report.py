import json
from pathlib import Path
from bench.report import persist_run, load_previous, emit_trend


def _payload(p95: float, cost: float) -> dict:
    return {
        "timestamp": 1.0,
        "stages": {"pipeline.guardrail": {"p50": 1, "p95": p95, "p99": 3, "mean": 1, "max": 3}},
        "cost_usd_mean": cost,
        "e2e": {"p95": p95},
    }


def test_persist_and_load_previous(tmp_path: Path):
    persist_run(_payload(10, 0.01), tmp_path)
    persist_run(_payload(20, 0.02), tmp_path)
    prev = load_previous(tmp_path)
    assert prev["cost_usd_mean"] == 0.01     # older of the two


def test_load_previous_none_when_single(tmp_path: Path):
    persist_run(_payload(10, 0.01), tmp_path)
    assert load_previous(tmp_path) is None


def test_emit_trend_shows_delta():
    md = emit_trend(_payload(20, 0.02), _payload(10, 0.01))
    assert "pipeline.guardrail" in md
    assert "+10" in md or "+10.00" in md
