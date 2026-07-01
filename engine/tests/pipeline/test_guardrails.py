import pytest
from app.pipeline.guardrails import QueryGuardrail, GuardrailViolation


@pytest.fixture
def guardrail(shipped_guardrail_config):
    return QueryGuardrail(
        known_indexes=["main", "security", "web"],
        max_time_range_days=shipped_guardrail_config["max_time_range_days"],
        resource_heavy_patterns=shipped_guardrail_config["resource_heavy_patterns"],
    )


def test_valid_spl_passes(guardrail):
    spl = 'index=main sourcetype=syslog earliest=-1d | stats count by host'
    result = guardrail.validate(spl)
    assert result.passed


def test_unknown_index_raises(guardrail):
    spl = 'index=nonexistent sourcetype=syslog earliest=-1d | stats count'
    with pytest.raises(GuardrailViolation, match="Unknown index"):
        guardrail.validate(spl)


def test_wildcard_index_passes(guardrail):
    spl = 'index=* sourcetype=syslog earliest=-1d | stats count'
    result = guardrail.validate(spl)
    assert result.passed


def test_missing_time_range_raises(guardrail):
    spl = 'index=main sourcetype=syslog | stats count'
    with pytest.raises(GuardrailViolation, match="No time range"):
        guardrail.validate(spl)


def test_excessive_time_range_raises(guardrail):
    spl = 'index=main sourcetype=syslog earliest=-90d | stats count'
    with pytest.raises(GuardrailViolation, match="exceeds max"):
        guardrail.validate(spl)


def test_14_day_range_passes_for_period_comparison(guardrail):
    """Comparison queries need earliest=-14d to compare current 7d to prior 7d."""
    spl = 'index=main sourcetype=syslog earliest=-14d | eval period=if(_time>=relative_time(now(),"-7d"),"current","previous") | stats sum(revenue) by period'
    result = guardrail.validate(spl)
    assert result.passed


def test_custom_max_time_range_enforced():
    """Caller-supplied max_time_range_days overrides default."""
    strict = QueryGuardrail(known_indexes=["main"], max_time_range_days=7, resource_heavy_patterns=[])
    spl = 'index=main earliest=-8d | stats count'
    with pytest.raises(GuardrailViolation, match="exceeds max 7d"):
        strict.validate(spl)


def test_7_day_range_passes(guardrail):
    spl = 'index=main sourcetype=syslog earliest=-7d | stats count'
    result = guardrail.validate(spl)
    assert result.passed


def test_unbounded_join_raises(guardrail):
    spl = 'index=main earliest=-1d | join type=inner host [search index=security earliest=-1d]'
    with pytest.raises(GuardrailViolation, match="Unbounded join"):
        guardrail.validate(spl)


def test_delete_command_raises(guardrail):
    spl = 'index=main earliest=-1d | delete'
    with pytest.raises(GuardrailViolation, match="delete command"):
        guardrail.validate(spl)


def test_outputlookup_raises(guardrail):
    spl = 'index=main earliest=-1d | outputlookup my_lookup'
    with pytest.raises(GuardrailViolation, match="outputlookup"):
        guardrail.validate(spl)


def test_no_known_indexes_skips_index_check():
    guardrail = QueryGuardrail(known_indexes=[], max_time_range_days=30, resource_heavy_patterns=[])
    spl = 'index=anything earliest=-1d | stats count'
    result = guardrail.validate(spl)
    assert result.passed


def test_update_known_indexes(guardrail):
    guardrail.update_known_indexes(["new_index"])
    spl = 'index=new_index earliest=-1d | stats count'
    result = guardrail.validate(spl)
    assert result.passed


# --- Case sensitivity ---

def test_delete_uppercase_raises(guardrail):
    spl = 'index=main earliest=-1d | DELETE'
    with pytest.raises(GuardrailViolation, match="delete command"):
        guardrail.validate(spl)


def test_join_mixed_case_raises(guardrail):
    spl = 'index=main earliest=-1d | Join host [search index=security]'
    with pytest.raises(GuardrailViolation, match="Unbounded join"):
        guardrail.validate(spl)


def test_outputlookup_uppercase_raises(guardrail):
    spl = 'index=main earliest=-1d | OUTPUTLOOKUP my_lookup'
    with pytest.raises(GuardrailViolation, match="outputlookup"):
        guardrail.validate(spl)


# --- Valid patterns that should pass ---

def test_join_with_max_passes(guardrail):
    spl = 'index=main earliest=-1d | join max=100 host [search index=security earliest=-1d]'
    result = guardrail.validate(spl)
    assert result.passed


def test_join_with_overwrite_passes(guardrail):
    spl = 'index=main earliest=-1d | join overwrite=true host [search index=security earliest=-1d]'
    result = guardrail.validate(spl)
    assert result.passed


def test_collect_with_index_passes(guardrail):
    spl = 'index=main earliest=-1d | collect index=summary_index'
    result = guardrail.validate(spl)
    assert result.passed


# --- Multiple violations ---

def test_multiple_violations_combined(guardrail):
    spl = 'index=nonexistent | delete'  # unknown index + no time range + delete
    with pytest.raises(GuardrailViolation) as exc_info:
        guardrail.validate(spl)
    reason = exc_info.value.reason
    assert "Unknown index" in reason
    assert "No time range" in reason
    assert "delete command" in reason


# --- Quoted indexes ---

def test_quoted_index_passes(guardrail):
    spl = 'index="main" sourcetype=syslog earliest=-1d | stats count'
    result = guardrail.validate(spl)
    assert result.passed


def test_single_quoted_index_passes(guardrail):
    spl = "index='main' sourcetype=syslog earliest=-1d | stats count"
    result = guardrail.validate(spl)
    assert result.passed


# --- Time range edge cases ---

def test_earliest_with_hours_passes(guardrail):
    spl = 'index=main earliest=-24h | stats count'
    result = guardrail.validate(spl)
    assert result.passed


def test_earliest_with_minutes_passes(guardrail):
    spl = 'index=main earliest=-60m | stats count'
    result = guardrail.validate(spl)
    assert result.passed


def test_exactly_max_days_passes(guardrail):
    """Boundary: exactly 30 days (default max) should pass."""
    spl = 'index=main earliest=-30d | stats count'
    result = guardrail.validate(spl)
    assert result.passed


def test_one_over_max_days_raises(guardrail):
    """Boundary: 31 days should fail (default max=30)."""
    spl = 'index=main earliest=-31d | stats count'
    with pytest.raises(GuardrailViolation, match="exceeds max"):
        guardrail.validate(spl)


# --- Collect without index ---

def test_collect_without_index_raises(guardrail):
    spl = 'index=main earliest=-1d | collect'
    with pytest.raises(GuardrailViolation, match="collect without target index"):
        guardrail.validate(spl)


def test_unbounded_join_with_max_in_later_stage_raises(guardrail):
    # max= appears in a later pipe stage (eval), not in the join stage itself
    # DOTALL false-negative: the lookahead (?!.*max) spans the whole string
    spl = 'index=main earliest=-1d | join host [search index=security earliest=-1d] | eval x=max(count, 0)'
    with pytest.raises(GuardrailViolation, match="Unbounded join"):
        guardrail.validate(spl)


def test_unbounded_join_with_stats_max_in_later_stage_raises(guardrail):
    # stats max(...) in a downstream stage must not satisfy the join lookahead
    spl = 'index=main earliest=-1d | join type=inner src_ip [search index=security earliest=-1d] | stats max(bytes) by src_ip'
    with pytest.raises(GuardrailViolation, match="Unbounded join"):
        guardrail.validate(spl)
