from __future__ import annotations
import hashlib
import logging
from pathlib import Path

import yaml

_logger = logging.getLogger("setuq.prompt_registry")

# Canonical set of prompts the pipeline requires. The text lives ONLY in the
# prompts YAML — there are no code defaults. Loaded at startup; any missing name
# is a hard failure (fail-closed) so an agent never runs without a prompt.
REQUIRED_PROMPTS: frozenset[str] = frozenset(
    {
        "spl_generator",
        "spl_explain",
        "summarizer",
        "action_suggester",
        "analysis_agent",
        "planner",
        "decision_engine",
    }
)

# Prompts loaded from YAML. Sole source of truth — populated by load().
_prompts: dict[str, str] = {}


class PromptConfigError(RuntimeError):
    """Raised when the prompt YAML is missing, malformed, or incomplete."""


def get(name: str) -> str:
    """Return the loaded prompt text for ``name``.

    Raises ``KeyError`` if the prompt was never loaded — agents must never run
    without a prompt (fail-closed).
    """
    return _prompts[name]


def version(name: str) -> str:
    """sha256[:8] of the loaded prompt text; unknown name -> hash of ""."""
    return hashlib.sha256(_prompts.get(name, "").encode()).hexdigest()[:8]


def all_versions() -> dict[str, str]:
    return {name: version(name) for name in _prompts}


def load(path: str) -> int:
    """Load prompts from a YAML file mapping prompt name -> text.

    Accepts either a flat map or a top-level ``prompts:`` key. Returns the count
    loaded. Raises ``PromptConfigError`` on a missing/malformed file or a
    non-string prompt value — there are no code defaults to fall back to.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise PromptConfigError(f"Prompt config not found: {path}")
    try:
        content = yaml.safe_load(file_path.read_text())
    except yaml.YAMLError as exc:
        raise PromptConfigError(f"Prompt config YAML malformed ({path}): {exc}") from exc
    if not isinstance(content, dict):
        raise PromptConfigError(f"Prompt config must be a mapping: {path}")
    mapping = content.get("prompts", content)
    if not isinstance(mapping, dict):
        raise PromptConfigError(f"'prompts' must be a mapping: {path}")
    loaded = 0
    for name, text in mapping.items():
        if not isinstance(text, str):
            raise PromptConfigError(f"Prompt '{name}' must be a string, got {type(text).__name__}")
        _prompts[name] = text
        loaded += 1
        _logger.info("Prompt loaded: %s (version=%s)", name, version(name))
    return loaded


def ensure_complete() -> None:
    """Raise ``PromptConfigError`` if any required prompt is missing.

    Fail-closed startup check — call after ``load()``.
    """
    missing = REQUIRED_PROMPTS - _prompts.keys()
    if missing:
        raise PromptConfigError(f"Missing required prompt(s): {', '.join(sorted(missing))}")
