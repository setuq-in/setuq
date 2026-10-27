import pytest
from opentelemetry import trace
from bench.capture import StageCapture


def test_capture_records_span_durations():
    with StageCapture() as cap:
        tracer = trace.get_tracer("test")
        with tracer.start_as_current_span("pipeline.guardrail"):
            pass
        durations = cap.durations_ms()
    assert "pipeline.guardrail" in durations
    assert durations["pipeline.guardrail"] >= 0.0


def test_capture_clear_resets():
    with StageCapture() as cap:
        tracer = trace.get_tracer("test")
        with tracer.start_as_current_span("pipeline.decide"):
            pass
        cap.clear()
        assert cap.durations_ms() == {}
