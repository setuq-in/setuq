"""Guardrail hardening — command denylist, wildcard/subsearch index, time bounds."""
import pytest

from app.pipeline.guardrails import QueryGuardrail, GuardrailViolation, load_guardrail_config
from tests.conftest import GUARDRAILS_PATH

# Load from the shipped YAML — the sole source of truth at runtime — rather
# than a hand-maintained constant, so this test can't drift from production
# wiring the way it previously did (see config/guardrails.yaml history).
_SHIPPED_CONFIG = load_guardrail_config(GUARDRAILS_PATH)


def _guard():
    return QueryGuardrail(
        known_indexes=["main", "security"],
        max_time_range_days=_SHIPPED_CONFIG["max_time_range_days"],
        resource_heavy_patterns=_SHIPPED_CONFIG["resource_heavy_patterns"],
    )


@pytest.mark.parametrize("cmd", ["script", "runshell", "sendemail", "rest"])
def test_dangerous_commands_blocked(cmd):
    guard = _guard()
    with pytest.raises(GuardrailViolation):
        guard.validate(f"index=main earliest=-1d | {cmd}")


def test_bare_wildcard_index_blocked():
    guard = _guard()
    with pytest.raises(GuardrailViolation, match="Wildcard index"):
        guard.validate("index=* earliest=-1d | stats count")


def test_partial_wildcard_index_allowed():
    guard = _guard()
    # sec* is a partial wildcard — allowed (can't enumerate against known set).
    assert guard.validate("index=sec* earliest=-1d | stats count").passed


def test_subsearch_unknown_index_flagged():
    guard = _guard()
    with pytest.raises(GuardrailViolation, match="Unknown index 'bogus'"):
        guard.validate("index=main earliest=-1d [ search index=bogus | head 1 ]")


def test_subsearch_known_index_allowed():
    guard = _guard()
    assert guard.validate(
        "index=main earliest=-1d [ search index=security earliest=-1d | head 1 ]"
    ).passed


def test_unbounded_minutes_blocked():
    guard = _guard()  # shipped max_time_range_days -> bounded minutes
    with pytest.raises(GuardrailViolation, match=f"exceeds max {_SHIPPED_CONFIG['max_time_range_days']}d"):
        guard.validate("index=main earliest=-999999999m | stats count")


def test_bounded_minutes_allowed():
    guard = _guard()
    assert guard.validate("index=main earliest=-30m | stats count").passed


def test_unbounded_seconds_blocked():
    guard = _guard()  # shipped max_time_range_days -> bounded seconds
    with pytest.raises(GuardrailViolation, match=f"exceeds max {_SHIPPED_CONFIG['max_time_range_days']}d"):
        guard.validate("index=main earliest=-9999999999s | stats count")
