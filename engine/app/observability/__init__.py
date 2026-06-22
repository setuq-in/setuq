def __getattr__(name):
    """Lazy load observability modules to avoid import errors when dependencies are not installed."""
    if name == "init_tracer":
        from app.observability.tracer import init_tracer
        return init_tracer
    elif name == "get_tracer":
        from app.observability.tracer import get_tracer
        return get_tracer
    elif name == "init_langfuse":
        from app.observability.langfuse_client import init_langfuse
        return init_langfuse
    elif name == "get_langfuse":
        from app.observability.langfuse_client import get_langfuse
        return get_langfuse
    elif name == "trace_step":
        from app.observability.decorators import trace_step
        return trace_step
    elif name == "trace_llm":
        from app.observability.decorators import trace_llm
        return trace_llm
    elif name == "hash_query":
        from app.observability.redact import hash_query
        return hash_query
    elif name == "scrub_dict":
        from app.observability.redact import scrub_dict
        return scrub_dict
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "init_tracer", "get_tracer",
    "init_langfuse", "get_langfuse",
    "trace_step", "trace_llm",
    "hash_query", "scrub_dict",
]
