from bench.metrics import percentile, summarize, LatencySummary


def test_percentile_basic():
    data = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert percentile(data, 50) == 5.5
    assert percentile(data, 100) == 10.0
    assert percentile(data, 0) == 1.0


def test_percentile_empty_is_zero():
    assert percentile([], 95) == 0.0


def test_summarize_fields():
    s = summarize([10.0, 20.0, 30.0, 40.0])
    assert isinstance(s, LatencySummary)
    assert s.count == 4
    assert s.max == 40.0
    assert s.mean == 25.0
    assert s.p50 == 25.0
