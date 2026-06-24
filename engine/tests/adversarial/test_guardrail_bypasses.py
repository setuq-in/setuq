"""Adversarial tests for guardrail bypass scenarios fixed in Sprint 1.

Covers:
- DOTALL fix: newline-separated pipe commands are caught
- Time grammar: hours, snap-relative, months, epoch-zero, exact boundaries
- Quoted-string pipe bypass: pipe inside a quoted value is not a command separator
- Standard block-listed commands: delete, outputlookup
- No time range enforcement
"""
import pytest
from app.pipeline.guardrails import QueryGuardrail, GuardrailViolation


@pytest.fixture
def guardrail():
    return QueryGuardrail(known_indexes=["myindex"], max_time_range_days=30)


# ---------------------------------------------------------------------------
# Newline bypass (DOTALL fix)
# ---------------------------------------------------------------------------

def test_delete_with_newline_caught(guardrail):
    """| followed by newline then delete must be caught (was bypassing before DOTALL fix)."""
    with pytest.raises(GuardrailViolation, match="delete"):
        guardrail.validate("index=myindex earliest=-1d |\ndelete foo")


def test_join_multiline_no_max_caught(guardrail):
    """join on one line, subsearch on next line with no max= must be caught."""
    with pytest.raises(GuardrailViolation):
        guardrail.validate(
            "index=myindex earliest=-1d | join host\n[ search index=myindex earliest=-1d ]"
        )


def test_outputlookup_with_newline_caught(guardrail):
    """| followed by newline then outputlookup must be caught."""
    with pytest.raises(GuardrailViolation, match="outputlookup"):
        guardrail.validate("index=myindex earliest=-1d |\noutputlookup myfile.csv")


def test_join_with_max_multiline_passes(guardrail):
    """join with max= on a subsequent line must still pass (max= anywhere in expression)."""
    result = guardrail.validate(
        "index=myindex earliest=-1d | join max=100 host\n[ search index=myindex earliest=-1d ]"
    )
    assert result.passed


# ---------------------------------------------------------------------------
# Time grammar bypasses
# ---------------------------------------------------------------------------

def test_earliest_epoch_zero_caught(guardrail):
    """earliest=0 is the epoch — must be caught."""
    with pytest.raises(GuardrailViolation):
        guardrail.validate("index=myindex earliest=0")


def test_earliest_epoch_one_caught(guardrail):
    """earliest=1 is effectively epoch — must be caught."""
    with pytest.raises(GuardrailViolation):
        guardrail.validate("index=myindex earliest=1")


def test_earliest_hours_exceeds_max_caught(guardrail):
    """800h is more than 30 days (720h) — must be caught."""
    with pytest.raises(GuardrailViolation):
        guardrail.validate("index=myindex earliest=-800h")


def test_earliest_hours_at_boundary_caught(guardrail):
    """721h slightly exceeds 30d (720h) — must be caught."""
    with pytest.raises(GuardrailViolation):
        guardrail.validate("index=myindex earliest=-721h")


def test_earliest_hours_within_max_passes(guardrail):
    """24h is well within 30 days (720h) — must pass."""
    result = guardrail.validate("index=myindex earliest=-24h")
    assert result.passed


def test_earliest_exactly_max_hours_passes(guardrail):
    """720h == exactly 30 days — must pass (boundary is inclusive)."""
    result = guardrail.validate("index=myindex earliest=-720h")
    assert result.passed


def test_earliest_snap_relative_caught(guardrail):
    """@d snap-relative is too complex to bound safely — must be caught."""
    with pytest.raises(GuardrailViolation):
        guardrail.validate("index=myindex earliest=@d-30d")


def test_earliest_snap_at_only_caught(guardrail):
    """earliest=@d alone must be caught."""
    with pytest.raises(GuardrailViolation):
        guardrail.validate("index=myindex earliest=@d")


def test_earliest_months_caught(guardrail):
    """Months unit is not allowed — must be caught."""
    with pytest.raises(GuardrailViolation):
        guardrail.validate("index=myindex earliest=-2mon")


def test_earliest_weeks_caught(guardrail):
    """Weeks unit is not allowed — must be caught."""
    with pytest.raises(GuardrailViolation):
        guardrail.validate("index=myindex earliest=-4w")


def test_earliest_days_exceeds_max_caught(guardrail):
    """60d exceeds the 30d max — must be caught."""
    with pytest.raises(GuardrailViolation):
        guardrail.validate("index=myindex earliest=-60d")


def test_earliest_days_one_over_max_caught(guardrail):
    """31d is one over max of 30d — must be caught."""
    with pytest.raises(GuardrailViolation):
        guardrail.validate("index=myindex earliest=-31d")


def test_earliest_days_within_max_passes(guardrail):
    """7d is within 30d max — must pass."""
    result = guardrail.validate("index=myindex earliest=-7d")
    assert result.passed


def test_earliest_exactly_max_days_passes(guardrail):
    """30d is exactly the max — must pass (boundary is inclusive)."""
    result = guardrail.validate("index=myindex earliest=-30d")
    assert result.passed


def test_earliest_minutes_passes(guardrail):
    """Minutes are fine — must pass."""
    result = guardrail.validate("index=myindex earliest=-60m")
    assert result.passed


# ---------------------------------------------------------------------------
# Quoted-string pipe bypass
# ---------------------------------------------------------------------------

def test_pipe_inside_quoted_string_not_split(guardrail):
    """A literal | inside a double-quoted value must not be treated as a command separator.

    Before the fix, splitting on | would parse 'fail"' as an index name and raise
    UnknownIndex, masking the real issue. After the fix, the quoted string is stripped
    before splitting, so the quoted | is not a separator.
    """
    spl = 'index=myindex earliest=-1d | eval msg=if(status="ok|fail", "yes", "no")'
    result = guardrail.validate(spl)
    assert result.passed


def test_pipe_inside_single_quoted_string_not_split(guardrail):
    """A literal | inside a single-quoted value must not be treated as a command separator."""
    spl = "index=myindex earliest=-1d | eval x=if(y='a|b', 1, 0)"
    result = guardrail.validate(spl)
    assert result.passed


def test_index_name_inside_quoted_string_not_matched(guardrail):
    """An index= inside a quoted string is not a real index reference.

    e.g. eval x='index=badindex' should not raise UnknownIndex.
    """
    spl = "index=myindex earliest=-1d | eval x='index=badindex'"
    result = guardrail.validate(spl)
    assert result.passed


# ---------------------------------------------------------------------------
# No time range enforcement
# ---------------------------------------------------------------------------

def test_no_time_range_caught(guardrail):
    """A query with no earliest= must be caught."""
    with pytest.raises(GuardrailViolation, match="No time range"):
        guardrail.validate("index=myindex | stats count")


def test_no_time_range_with_complex_pipeline_caught(guardrail):
    """Complex pipeline without earliest= must still be caught."""
    with pytest.raises(GuardrailViolation, match="No time range"):
        guardrail.validate("index=myindex | stats count by host | sort -count | head 10")


# ---------------------------------------------------------------------------
# Standard blocked commands
# ---------------------------------------------------------------------------

def test_outputlookup_caught(guardrail):
    """outputlookup must always be blocked."""
    with pytest.raises(GuardrailViolation, match="outputlookup"):
        guardrail.validate("index=myindex earliest=-1d | outputlookup myfile.csv")


def test_delete_inline_caught(guardrail):
    """Inline delete (no newline) must be caught."""
    with pytest.raises(GuardrailViolation, match="delete"):
        guardrail.validate("index=myindex earliest=-1d | delete")


def test_collect_without_index_caught(guardrail):
    """collect without index= target must be caught."""
    with pytest.raises(GuardrailViolation, match="collect without target index"):
        guardrail.validate("index=myindex earliest=-1d | collect")
