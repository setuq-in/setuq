"""Tests for prompt_registry — YAML is the sole source of truth."""
import pytest
import app.pipeline.prompt_registry as reg


def test_get_returns_loaded_prompt(tmp_path):
    reg._prompts.clear()
    f = tmp_path / "prompts.yaml"
    f.write_text("prompts:\n  planner: You are a test planner.\n")
    reg.load(str(f))
    assert reg.get("planner") == "You are a test planner."


def test_get_missing_raises_keyerror():
    reg._prompts.clear()
    with pytest.raises(KeyError):
        reg.get("planner")


def test_version_is_stable_and_8_chars(tmp_path):
    reg._prompts.clear()
    f = tmp_path / "p.yaml"
    f.write_text("prompts:\n  summarizer: My prompt text\n")
    reg.load(str(f))
    v1 = reg.version("summarizer")
    assert v1 == reg.version("summarizer")
    assert len(v1) == 8


def test_version_changes_with_text(tmp_path):
    reg._prompts.clear()
    (tmp_path / "a.yaml").write_text("prompts:\n  summarizer: Version 1\n")
    reg.load(str(tmp_path / "a.yaml"))
    v1 = reg.version("summarizer")
    reg._prompts.clear()
    (tmp_path / "b.yaml").write_text("prompts:\n  summarizer: Version 2\n")
    reg.load(str(tmp_path / "b.yaml"))
    assert reg.version("summarizer") != v1


def test_flat_mapping_without_prompts_key(tmp_path):
    reg._prompts.clear()
    f = tmp_path / "p.yaml"
    f.write_text("planner: flat text\n")
    assert reg.load(str(f)) == 1
    assert reg.get("planner") == "flat text"


def test_load_missing_file_raises():
    with pytest.raises(reg.PromptConfigError):
        reg.load("does/not/exist.yaml")


def test_load_malformed_raises(tmp_path):
    f = tmp_path / "bad.yaml"
    f.write_text("prompts:\n  planner: [unclosed\n")
    with pytest.raises(reg.PromptConfigError):
        reg.load(str(f))


def test_load_non_string_value_raises(tmp_path):
    f = tmp_path / "p.yaml"
    f.write_text("prompts:\n  planner: 42\n")
    with pytest.raises(reg.PromptConfigError):
        reg.load(str(f))


def test_version_empty_string_for_unknown():
    import hashlib
    reg._prompts.clear()
    expected = hashlib.sha256(b"").hexdigest()[:8]
    assert reg.version("nonexistent") == expected


def test_ensure_complete_passes_for_shipped_config():
    # autouse conftest fixture already loaded the shipped prompts
    reg.ensure_complete()


def test_ensure_complete_raises_when_incomplete(tmp_path):
    reg._prompts.clear()
    f = tmp_path / "p.yaml"
    f.write_text("prompts:\n  planner: only one\n")
    reg.load(str(f))
    with pytest.raises(reg.PromptConfigError, match="Missing required prompt"):
        reg.ensure_complete()


def test_all_versions_covers_required():
    versions = reg.all_versions()
    for name in reg.REQUIRED_PROMPTS:
        assert name in versions
        assert len(versions[name]) == 8
