"""Guardrail hardening — command denylist, wildcard/subsearch index, time bounds."""
import pytest

from app.pipeline.guardrails import QueryGuardrail, GuardrailViolation


def _guard():
    return QueryGuardrail(known_indexes=["main", "security"], max_time_range_days=30)


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
    guard = _guard()  # 30d max -> 43200 minutes
    with pytest.raises(GuardrailViolation, match="exceeds max 30d"):
        guard.validate("index=main earliest=-999999m | stats count")


def test_bounded_minutes_allowed():
    guard = _guard()
    assert guard.validate("index=main earliest=-30m | stats count").passed


def test_unbounded_seconds_blocked():
    guard = _guard()  # 30d max -> 2_592_000 seconds
    with pytest.raises(GuardrailViolation, match="exceeds max 30d"):
        guard.validate("index=main earliest=-9999999999s | stats count")
