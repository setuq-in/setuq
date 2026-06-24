"""Unit tests for eval runner scoring logic (no Splunk/LLM required)."""
import pytest
from eval.runner import _score_keywords, _load_golden_queries, EvalResult
from pathlib import Path


def test_score_keywords_all_present():
    spl = "index=security | stats count by src_ip | sort -count"
    assert _score_keywords(spl, ["index", "stats", "count", "by", "src_ip"]) == 1.0


def test_score_keywords_partial():
    spl = "index=security | stats count"
    score = _score_keywords(spl, ["index", "stats", "count", "src_ip"])
    assert score == 0.75


def test_score_keywords_none_present():
    spl = "index=main"
    assert _score_keywords(spl, ["stats", "count", "by"]) == 0.0


def test_score_keywords_empty_list():
    assert _score_keywords("any spl", []) == 1.0


def test_score_keywords_case_insensitive():
    spl = "INDEX=security | STATS COUNT"
    assert _score_keywords(spl, ["index", "stats", "count"]) == 1.0


def test_load_golden_queries_returns_list():
    queries = _load_golden_queries()
    assert isinstance(queries, list)
    assert len(queries) >= 40
    for q in queries:
        assert "id" in q
        assert "query" in q


def test_eval_result_dataclass():
    r = EvalResult(
        query_id="gq-001",
        query="show failed logins",
        category="easy",
        spl="index=security | stats count",
        passed_keyword_check=True,
        passed_guardrail_check=True,
        passed_plan_check=True,
        passed_decision_check=True,
        keyword_score=1.0,
        spl_quality={"validity": 1.0, "groundedness": 1.0, "time_hygiene": 1.0},
        judge_rubric={"correctness": 4, "safety": 5, "conciseness": 4, "citation": 4},
        decision_precision=1.0,
        execution_time_ms=250,
        error=None,
        prompt_versions={},
    )
    assert r.query_id == "gq-001"
    assert r.judge_rubric["correctness"] == 4


def test_all_golden_queries_have_required_fields():
    queries = _load_golden_queries()
    for q in queries:
        # expected_spl_keywords is only required for queries we expect to produce SPL
        if q.get("category") in (None, "easy", "multi_step", "unknown_index"):
            assert "expected_spl_keywords" in q, f"{q['id']} missing expected_spl_keywords"
            assert isinstance(q["expected_spl_keywords"], list)
        assert isinstance(q.get("expected_no_guardrail_violation", True), bool)
