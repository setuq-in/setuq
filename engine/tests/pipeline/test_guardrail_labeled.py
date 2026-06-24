"""Sprint 6 — Labeled guardrail FP/FN set. SPL fixtures with known safe/unsafe labels."""
import pytest
from app.pipeline.guardrails import QueryGuardrail, GuardrailViolation


SAFE_SPL = [
    "index=main earliest=-1d | stats count by user",
    "index=auth sourcetype=login earliest=-1h | top src_ip",
    "index=firewall earliest=-24h | stats sum(bytes) by dest_ip",
]

UNSAFE_SPL = [
    "index=main earliest=-5y | delete",
    "index=* | head 1000000",
    "index=main earliest=-365d | outputlookup steal.csv",
]


@pytest.fixture
def guard():
    return QueryGuardrail(known_indexes=["main", "auth", "firewall"])


@pytest.mark.parametrize("spl", SAFE_SPL)
def test_safe_spl_passes(guard, spl):
    """False-positive check: labeled-safe SPL must NOT raise."""
    result = guard.validate(spl)
    assert result.passed, f"safe SPL falsely rejected: {spl} — {result.violations}"


@pytest.mark.parametrize("spl", UNSAFE_SPL)
def test_unsafe_spl_blocked(guard, spl):
    """False-negative check: labeled-unsafe SPL must raise GuardrailViolation."""
    with pytest.raises(GuardrailViolation):
        guard.validate(spl)


def test_fp_fn_rates(guard):
    """Aggregate metric: FP and FN rates must both be 0 on the labeled set."""
    fp = 0
    fn = 0
    for spl in SAFE_SPL:
        try:
            guard.validate(spl)
        except GuardrailViolation:
            fp += 1
    for spl in UNSAFE_SPL:
        try:
            guard.validate(spl)
        except GuardrailViolation:
            pass
        else:
            fn += 1
    fp_rate = fp / len(SAFE_SPL)
    fn_rate = fn / len(UNSAFE_SPL)
    assert fp_rate == 0.0, f"guardrail false-positive rate {fp_rate:.2f}"
    assert fn_rate == 0.0, f"guardrail false-negative rate {fn_rate:.2f}"
