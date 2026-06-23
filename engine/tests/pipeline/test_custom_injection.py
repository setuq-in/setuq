"""Custom prompt + guardrail injection from YAML files."""
import pytest

import app.pipeline.prompt_registry as reg
from app.pipeline.guardrails import (
    QueryGuardrail,
    GuardrailViolation,
    load_guardrail_config,
)


def setup_function():
    reg._registry.clear()
    reg._overrides.clear()


# ── Prompt overrides ─────────────────────────────────────────────────────────

def test_override_replaces_default(tmp_path):
    reg.register("planner", "DEFAULT planner prompt")
    f = tmp_path / "prompts.yaml"
    f.write_text("prompts:\n  planner: CUSTOM planner prompt\n")

    applied = reg.load_overrides(str(f))

    assert applied == 1
    assert reg.get("planner") == "CUSTOM planner prompt"


def test_override_changes_version_hash(tmp_path):
    reg.register("summarizer", "default text")
    before = reg.version("summarizer")
    f = tmp_path / "prompts.yaml"
    f.write_text("prompts:\n  summarizer: brand new text\n")

    reg.load_overrides(str(f))

    assert reg.version("summarizer") != before


def test_flat_mapping_without_prompts_key(tmp_path):
    reg.register("planner", "default")
    f = tmp_path / "prompts.yaml"
    f.write_text("planner: flat override\n")

    assert reg.load_overrides(str(f)) == 1
    assert reg.get("planner") == "flat override"


def test_unknown_prompt_name_ignored(tmp_path):
    reg.register("planner", "default")
    f = tmp_path / "prompts.yaml"
    f.write_text("prompts:\n  not_a_real_agent: hi\n")

    assert reg.load_overrides(str(f)) == 0
    assert "not_a_real_agent" not in reg._overrides


def test_unregistered_default_still_used_when_no_override():
    reg.register("planner", "the default")
    assert reg.get("planner") == "the default"


def test_missing_file_is_noop():
    assert reg.load_overrides("does/not/exist.yaml") == 0


def test_malformed_yaml_is_noop(tmp_path):
    reg.register("planner", "default")
    f = tmp_path / "bad.yaml"
    f.write_text("prompts:\n  planner: [unclosed\n")

    assert reg.load_overrides(str(f)) == 0
    assert reg.get("planner") == "default"


def test_non_string_override_skipped(tmp_path):
    reg.register("planner", "default")
    f = tmp_path / "prompts.yaml"
    f.write_text("prompts:\n  planner: 42\n")

    assert reg.load_overrides(str(f)) == 0
    assert reg.get("planner") == "default"


# ── Guardrail overrides ──────────────────────────────────────────────────────

def test_load_guardrail_config_parses_patterns_and_days(tmp_path):
    f = tmp_path / "guardrails.yaml"
    f.write_text(
        "max_time_range_days: 7\n"
        "resource_heavy_patterns:\n"
        "  - pattern: '\\|\\s*mycmd\\b'\n"
        "    reason: mycmd blocked\n"
    )

    config = load_guardrail_config(str(f))

    assert config["max_time_range_days"] == 7
    assert config["resource_heavy_patterns"] == [(r"\|\s*mycmd\b", "mycmd blocked")]


def test_injected_pattern_blocks_spl(tmp_path):
    f = tmp_path / "guardrails.yaml"
    f.write_text(
        "resource_heavy_patterns:\n"
        "  - pattern: '\\|\\s*mycmd\\b'\n"
        "    reason: mycmd blocked\n"
    )
    config = load_guardrail_config(str(f))
    guard = QueryGuardrail(
        known_indexes=["main"],
        resource_heavy_patterns=config["resource_heavy_patterns"],
    )

    with pytest.raises(GuardrailViolation, match="mycmd blocked"):
        guard.validate("index=main earliest=-1d | mycmd")

    # A default-blocked command (delete) is NOT in the override list -> allowed now.
    assert guard.validate("index=main earliest=-1d | delete").passed


def test_custom_max_time_range_enforced(tmp_path):
    f = tmp_path / "guardrails.yaml"
    f.write_text("max_time_range_days: 7\n")
    config = load_guardrail_config(str(f))
    guard = QueryGuardrail(known_indexes=["main"], max_time_range_days=config["max_time_range_days"])

    with pytest.raises(GuardrailViolation, match="exceeds max 7d"):
        guard.validate("index=main earliest=-30d | stats count")


def test_default_patterns_used_when_none():
    guard = QueryGuardrail(known_indexes=["main"])  # resource_heavy_patterns defaults
    with pytest.raises(GuardrailViolation):
        guard.validate("index=main earliest=-1d | delete")


def test_invalid_pattern_skipped(tmp_path):
    f = tmp_path / "guardrails.yaml"
    f.write_text(
        "resource_heavy_patterns:\n"
        "  - pattern: '([unclosed'\n"
        "    reason: bad regex\n"
        "  - pattern: '\\|\\s*mycmd\\b'\n"
        "    reason: good\n"
    )
    config = load_guardrail_config(str(f))
    # Bad regex dropped, good one kept.
    assert config["resource_heavy_patterns"] == [(r"\|\s*mycmd\b", "good")]


def test_missing_guardrail_file_returns_empty():
    assert load_guardrail_config("nope/missing.yaml") == {}
