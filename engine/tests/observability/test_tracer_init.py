import pytest
from unittest.mock import patch, MagicMock
from app.observability.tracer import init_tracer, get_tracer, shutdown_tracer


class _DisabledSettings:
    OBSERVABILITY_ENABLED = False
    OTEL_EXPORTER_OTLP_ENDPOINT = ""


class _EnabledSettings:
    OBSERVABILITY_ENABLED = True
    OTEL_EXPORTER_OTLP_ENDPOINT = ""  # no exporter, just in-process


def test_init_tracer_disabled_returns_none():
    result = init_tracer(_DisabledSettings())
    assert result is None


def test_init_tracer_enabled_returns_provider():
    from opentelemetry.sdk.trace import TracerProvider
    provider = init_tracer(_EnabledSettings())
    assert isinstance(provider, TracerProvider)
    shutdown_tracer()


def test_get_tracer_returns_tracer():
    from opentelemetry import trace
    tracer = get_tracer("test")
    assert tracer is not None


def test_disabled_tracer_produces_noop_spans():
    """With no provider set, spans must not raise."""
    tracer = get_tracer("test.noop")
    with tracer.start_as_current_span("noop.span") as span:
        span.set_attribute("key", "value")
    # no exception = pass
