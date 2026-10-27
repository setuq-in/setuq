import importlib

# Attribute -> defining module. Imported lazily so a missing optional
# observability dependency never breaks importing this package.
_LAZY_ATTRS = {
    "init_tracer": "app.observability.tracer",
    "get_tracer": "app.observability.tracer",
    "init_langfuse": "app.observability.langfuse_client",
    "get_langfuse": "app.observability.langfuse_client",
    "configure_logging": "app.observability.logging_setup",
    "trace_step": "app.observability.decorators",
    "trace_llm": "app.observability.decorators",
    "hash_query": "app.observability.redact",
    "scrub_dict": "app.observability.redact",
}

__all__ = list(_LAZY_ATTRS)


def __getattr__(name):
    module = _LAZY_ATTRS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module), name)
