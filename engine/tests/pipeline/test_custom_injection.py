"""Custom prompt + guardrail config from YAML — sole source of truth."""
import pytest

import app.pipeline.prompt_registry as reg
from app.pipeline.guardrails import (
    QueryGuardrail,
    GuardrailViolation,
    GuardrailConfigError,
    load_guardrail_config,
)


# ── Prompt loading ───────────────────────────────────────────────────────────

def test_loaded_prompt_is_used(tmp_path):
    reg._prompts.clear()
    f = tmp_path / "prompts.yaml"
    f.write_text("prompts:\n  planner: CUSTOM planner prompt\n")
    reg.load(str(f))
    assert reg.get("planner") == "CUSTOM planner prompt"


def test_editing_prompt_changes_version(tmp_path):
    reg._prompts.clear()
    (tmp_path / "a.yaml").write_text("prompts:\n  summarizer: default text\n")
    reg.load(str(tmp_path / "a.yaml"))
    before = reg.version("summarizer")
    reg._prompts.clear()
    (tmp_path / "b.yaml").write_text("prompts:\n  summarizer: brand new text\n")
    reg.load(str(tmp_path / "b.yaml"))
    assert reg.version("summarizer") != before


def test_missing_required_prompt_fails_closed(tmp_path):
    reg._prompts.clear()
    f = tmp_path / "prompts.yaml"
    f.write_text("prompts:\n  planner: only planner\n")
    reg.load(str(f))
    with pytest.raises(reg.PromptConfigError):
        reg.ensure_complete()


def test_missing_prompt_file_fails_closed():
    with pytest.raises(reg.PromptConfigError):
        reg.load("does/not/exist.yaml")


# ── Guardrail config ─────────────────────────────────────────────────────────

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
        "max_time_range_days: 30\n"
        "resource_heavy_patterns:\n"
        "  - pattern: '\\|\\s*mycmd\\b'\n"
        "    reason: mycmd blocked\n"
    )
    config = load_guardrail_config(str(f))
    guard = QueryGuardrail(
        known_indexes=["main"],
        max_time_range_days=config["max_time_range_days"],
        resource_heavy_patterns=config["resource_heavy_patterns"],
    )

    with pytest.raises(GuardrailViolation, match="mycmd blocked"):
        guard.validate("index=main earliest=-1d | mycmd")

    # delete is NOT in the override list -> allowed now.
    assert guard.validate("index=main earliest=-1d | delete").passed


def test_custom_max_time_range_enforced(tmp_path):
    f = tmp_path / "guardrails.yaml"
    f.write_text("max_time_range_days: 7\nresource_heavy_patterns: []\n")
    config = load_guardrail_config(str(f))
    guard = QueryGuardrail(
        known_indexes=["main"],
        max_time_range_days=config["max_time_range_days"],
        resource_heavy_patterns=config["resource_heavy_patterns"],
    )

    with pytest.raises(GuardrailViolation, match="exceeds max 7d"):
        guard.validate("index=main earliest=-30d | stats count")


def test_empty_patterns_list_allowed(tmp_path):
    f = tmp_path / "guardrails.yaml"
    f.write_text("max_time_range_days: 30\nresource_heavy_patterns: []\n")
    config = load_guardrail_config(str(f))
    assert config["resource_heavy_patterns"] == []


def test_invalid_pattern_skipped(tmp_path):
    f = tmp_path / "guardrails.yaml"
    f.write_text(
        "max_time_range_days: 30\n"
        "resource_heavy_patterns:\n"
        "  - pattern: '([unclosed'\n"
        "    reason: bad regex\n"
        "  - pattern: '\\|\\s*mycmd\\b'\n"
        "    reason: good\n"
    )
    config = load_guardrail_config(str(f))
    # Bad regex dropped, good one kept.
    assert config["resource_heavy_patterns"] == [(r"\|\s*mycmd\b", "good")]


def test_missing_guardrail_file_fails_closed():
    with pytest.raises(GuardrailConfigError):
        load_guardrail_config("nope/missing.yaml")


def test_missing_required_key_fails_closed(tmp_path):
    f = tmp_path / "guardrails.yaml"
    f.write_text("max_time_range_days: 30\n")  # resource_heavy_patterns absent
    with pytest.raises(GuardrailConfigError):
        load_guardrail_config(str(f))
