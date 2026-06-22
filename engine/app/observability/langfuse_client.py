from __future__ import annotations
from typing import Optional

_langfuse_instance = None


def init_langfuse(settings) -> Optional[object]:
    global _langfuse_instance
    if not settings.OBSERVABILITY_ENABLED:
        return None
    if not (settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY):
        return None
    try:
        from langfuse import Langfuse
        _langfuse_instance = Langfuse(
            public_key=settings.LANGFUSE_PUBLIC_KEY,
            secret_key=settings.LANGFUSE_SECRET_KEY,
            host=settings.LANGFUSE_HOST,
        )
        return _langfuse_instance
    except ImportError:
        return None


def get_langfuse():
    return _langfuse_instance


def flush_langfuse() -> None:
    if _langfuse_instance:
        _langfuse_instance.flush()
