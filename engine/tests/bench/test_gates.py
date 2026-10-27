from bench.gates import check_slo, check_regression, all_passed


def _payload(e2e_p95, guard_p95, cost):
    return {
        "stages": {"pipeline.guardrail": {"p95": guard_p95}},
        "e2e": {"p95": e2e_p95},
        "cost_usd_mean": cost,
    }


def test_slo_fails_when_over_threshold():
    res = check_slo(_payload(9000, 2, 0.01),
                    {"e2e_p95_ms": 6000, "guardrail_p95_ms": 5, "cost_usd_mean": 0.05})
    assert not all_passed(res)
    assert any(r.name == "e2e_p95_ms" and not r.passed for r in res)


def test_slo_passes_within_threshold():
    res = check_slo(_payload(3000, 2, 0.01),
                    {"e2e_p95_ms": 6000, "guardrail_p95_ms": 5, "cost_usd_mean": 0.05})
    assert all_passed(res)


def test_regression_flags_growth():
    cur = _payload(6000, 20, 0.01)
    prev = _payload(6000, 10, 0.01)
    res = check_regression(cur, prev, pct=0.5)     # 100% growth > 50%
    assert not all_passed(res)


def test_regression_none_without_previous():
    res = check_regression(_payload(1, 1, 0.01), None, pct=0.5)
    assert all_passed(res)


def test_regression_passes_within_threshold():
    cur = _payload(6000, 12, 0.01)
    prev = _payload(6000, 10, 0.01)
    res = check_regression(cur, prev, pct=0.5)     # 20% growth < 50% threshold
    assert all_passed(res)
