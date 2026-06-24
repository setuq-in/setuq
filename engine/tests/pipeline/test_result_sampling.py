"""Tests for stratified result sampling utility."""
import pytest
from app.pipeline.result_sampling import sample_for_llm


def _rows(n: int) -> list[dict]:
    return [{"id": i, "value": f"v{i}"} for i in range(n)]


def test_small_result_set_returned_intact():
    rows = _rows(10)
    sample, sketch = sample_for_llm(rows, k=60)
    assert sample == rows
    assert sketch["total_rows"] == 10


def test_large_result_set_truncated_to_k():
    rows = _rows(300)
    sample, sketch = sample_for_llm(rows, k=60)
    assert len(sample) <= 60
    assert sketch["total_rows"] == 300
    assert sketch["sampled"] == len(sample)


def test_head_and_tail_both_present():
    """First and last rows must always appear in the sample."""
    rows = _rows(200)
    sample, _ = sample_for_llm(rows, k=30)
    sample_ids = {r["id"] for r in sample}
    assert 0 in sample_ids, "First row (id=0) missing from sample"
    assert 199 in sample_ids, "Last row (id=199) missing from sample"


def test_sketch_includes_field_names():
    rows = [{"host": "a", "count": 1}, {"host": "b", "src_ip": "1.2.3.4"}]
    _, sketch = sample_for_llm(rows, k=60)
    assert "count" in sketch["fields"]
    assert "host" in sketch["fields"]
    assert "src_ip" in sketch["fields"]


def test_empty_rows_returns_empty():
    sample, sketch = sample_for_llm([], k=60)
    assert sample == []
    assert sketch["total_rows"] == 0
