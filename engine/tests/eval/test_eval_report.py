"""Sprint 6 — Eval trend report regression detection."""
import json
import time
from dataclasses import asdict
from pathlib import Path
import pytest
from eval.runner import EvalResult, _emit_trend_report, _persist_run, _RUNS_DIR


def _mk_result(qid: str, kw: float, correctness: int = 4) -> EvalResult:
    return EvalResult(
        query_id=qid,
        query="q",
        category="easy",
        spl="index=main earliest=-1d",
        passed_keyword_check=kw >= 0.5,
        passed_guardrail_check=True,
        passed_plan_check=True,
        passed_decision_check=True,
        keyword_score=kw,
        spl_quality={"validity": 1.0, "groundedness": 1.0, "time_hygiene": 1.0},
        judge_rubric={"correctness": correctness, "safety": 5, "conciseness": 4, "citation": 4},
        decision_precision=1.0,
        execution_time_ms=10,
        error=None,
        prompt_versions={"planner": "abc12345", "spl_generator": "def67890"},
    )


def test_no_previous_run_returns_placeholder(tmp_path, monkeypatch):
    monkeypatch.setattr("eval.runner._RUNS_DIR", tmp_path / "runs")
    results = [_mk_result("gq-001", 0.8)]
    report = _emit_trend_report(results)
    assert "No previous run" in report


def test_regression_detection(tmp_path, monkeypatch):
    runs_dir = tmp_path / "runs"
    monkeypatch.setattr("eval.runner._RUNS_DIR", runs_dir)

    # Previous run: kw=0.9, correctness=5
    prev_results = [_mk_result("gq-001", 0.9, correctness=5)]
    _persist_run(prev_results)
    time.sleep(1.1)  # ensure different timestamp filename

    # Current run: kw=0.2 (big drop), correctness=2 (big drop)
    cur_results = [_mk_result("gq-001", 0.2, correctness=2)]
    _persist_run(cur_results)

    report = _emit_trend_report(cur_results)
    assert "REGRESSION-KW" in report
    assert "REGRESSION-JUDGE" in report
    assert "Regressions:** 2" in report


def test_prompt_hash_change_surfaced(tmp_path, monkeypatch):
    runs_dir = tmp_path / "runs"
    monkeypatch.setattr("eval.runner._RUNS_DIR", runs_dir)

    prev_results = [_mk_result("gq-001", 0.8)]
    _persist_run(prev_results)
    time.sleep(1.1)

    cur = _mk_result("gq-001", 0.8)
    cur.prompt_versions = {"planner": "NEWHASH1", "spl_generator": "def67890"}
    cur_results = [cur]
    _persist_run(cur_results)

    report = _emit_trend_report(cur_results)
    assert "planner" in report
    assert "NEWHASH1" in report
    assert "abc12345" in report  # old hash shown for context
