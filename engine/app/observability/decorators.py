from __future__ import annotations
import functools
import time
from typing import Any, Callable
from opentelemetry import trace
from opentelemetry.trace import StatusCode


def trace_step(name: str):
    """Wrap async pipeline step in an OTel span."""
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            tracer = trace.get_tracer("setuq")
            with tracer.start_as_current_span(name) as span:
                try:
                    return await fn(*args, **kwargs)
                except Exception as exc:
                    span.set_status(StatusCode.ERROR, str(exc))
                    span.record_exception(exc)
                    raise
        return wrapper
    return decorator


def trace_llm(provider: str):
    """Wrap LLM provider generate() with GenAI semconv spans + Langfuse generation."""
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(self, *args, **kwargs):
            tracer = trace.get_tracer("setuq")
            start = time.monotonic()
            with tracer.start_as_current_span(f"llm.{provider}.generate") as span:
                span.set_attribute("gen_ai.system", provider)
                try:
                    result = await fn(self, *args, **kwargs)
                    elapsed_ms = int((time.monotonic() - start) * 1000)
                    if hasattr(result, "usage") and result.usage:
                        u = result.usage
                        span.set_attribute("gen_ai.request.model", u.model)
                        span.set_attribute("gen_ai.usage.input_tokens", u.input_tokens)
                        span.set_attribute("gen_ai.usage.output_tokens", u.output_tokens)
                        span.set_attribute("gen_ai.usage.cost_usd", u.cost_usd)
                        span.set_attribute("gen_ai.latency_ms", elapsed_ms)
                    return result
                except Exception as exc:
                    span.set_status(StatusCode.ERROR, str(exc))
                    span.record_exception(exc)
                    raise
        return wrapper
    return decorator
