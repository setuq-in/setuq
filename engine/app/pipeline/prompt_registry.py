from __future__ import annotations
import hashlib

_registry: dict[str, str] = {}


def register(name: str, text: str) -> str:
    """Register a prompt by name, return version hash."""
    _registry[name] = text
    return version(name)


def get(name: str) -> str:
    return _registry[name]


def version(name: str) -> str:
    """sha256[:8] of prompt text — stable across runs."""
    text = _registry.get(name, "")
    return hashlib.sha256(text.encode()).hexdigest()[:8]


def all_versions() -> dict[str, str]:
    return {name: version(name) for name in _registry}
