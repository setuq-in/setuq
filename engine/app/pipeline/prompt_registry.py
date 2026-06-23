from __future__ import annotations
import hashlib
import logging
from pathlib import Path

import yaml

_logger = logging.getLogger("setuq.prompt_registry")

# Code defaults — populated by register() at module import.
_registry: dict[str, str] = {}
# User overrides loaded from YAML — take precedence over code defaults.
_overrides: dict[str, str] = {}


def register(name: str, text: str) -> str:
    """Register a prompt default by name, return effective version hash."""
    _registry[name] = text
    return version(name)


def get(name: str) -> str:
    """Return the effective prompt: YAML override if present, else code default."""
    if name in _overrides:
        return _overrides[name]
    return _registry[name]


def resolve(name: str, default: str) -> str:
    """Return the YAML override for ``name`` if loaded, else ``default``.

    Agents pass their own code-default constant so resolution never depends on
    import-time registration having run — the registry only supplies overrides.
    """
    return _overrides.get(name, default)


def version(name: str) -> str:
    """sha256[:8] of the *effective* prompt text — stable across runs.

    Reflects the override when one is loaded so a customized prompt yields a
    distinct version hash (traceable in Langfuse/OTel).
    """
    text = _overrides.get(name) or _registry.get(name, "")
    return hashlib.sha256(text.encode()).hexdigest()[:8]


def all_versions() -> dict[str, str]:
    return {name: version(name) for name in _registry}


def load_overrides(path: str) -> int:
    """Load prompt overrides from a YAML file mapping prompt name -> text.

    Best-effort: a missing file, empty file, or malformed YAML leaves code
    defaults in place. Only keys matching a registered prompt name are applied
    so typos can't silently inject dead prompts. Returns the count applied.
    """
    file_path = Path(path)
    if not file_path.exists():
        return 0
    try:
        content = yaml.safe_load(file_path.read_text())
    except yaml.YAMLError as exc:
        _logger.warning("Prompt overrides YAML malformed (%s) — using code defaults: %s", path, exc)
        return 0
    if not isinstance(content, dict):
        return 0
    # Allow either a flat map or a top-level "prompts:" key.
    mapping = content.get("prompts", content)
    if not isinstance(mapping, dict):
        return 0
    applied = 0
    for name, text in mapping.items():
        if not isinstance(text, str):
            continue
        if name not in _registry:
            _logger.warning("Prompt override '%s' has no registered default — ignored", name)
            continue
        _overrides[name] = text
        applied += 1
        _logger.info("Prompt override applied: %s (version=%s)", name, version(name))
    return applied
