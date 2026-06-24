"""Sprint 3 — OTel circuit-breaking exporter: opens after N consecutive failures."""
from unittest.mock import MagicMock
from opentelemetry.sdk.trace.export import SpanExportResult
from app.observability.tracer import _CircuitBreakingExporter


def _make_failing_exporter() -> MagicMock:
    """Return a mock SpanExporter that always returns FAILURE."""
    mock = MagicMock()
    mock.export.return_value = SpanExportResult.FAILURE
    return mock


def test_circuit_opens_after_threshold_failures():
    """After threshold consecutive failures the circuit should be open."""
    threshold = 5
    inner = _make_failing_exporter()
    exporter = _CircuitBreakingExporter(inner, threshold=threshold)

    for _ in range(threshold):
        exporter.export([])

    assert exporter._open is True


def test_open_circuit_stops_forwarding():
    """Once open, the wrapped exporter must NOT be called again."""
    threshold = 3
    inner = _make_failing_exporter()
    exporter = _CircuitBreakingExporter(inner, threshold=threshold)

    # Trip the circuit
    for _ in range(threshold):
        exporter.export([])

    assert exporter._open is True
    call_count_after_open = inner.export.call_count  # should equal threshold

    # One more call — must not reach the inner exporter
    result = exporter.export([])
    assert inner.export.call_count == call_count_after_open, (
        "Wrapped exporter was called even though circuit is open"
    )
    # And the call should still return SUCCESS (fail-open behaviour)
    assert result == SpanExportResult.SUCCESS


def test_circuit_still_closed_before_threshold():
    """Circuit must not open before threshold consecutive failures."""
    threshold = 5
    inner = _make_failing_exporter()
    exporter = _CircuitBreakingExporter(inner, threshold=threshold)

    for _ in range(threshold - 1):
        exporter.export([])

    assert exporter._open is False
    assert inner.export.call_count == threshold - 1


def test_success_resets_failure_counter():
    """A successful export resets the failure counter, delaying circuit opening."""
    threshold = 3
    inner = MagicMock()
    # Fail twice, succeed once, fail threshold times → total failures since reset = threshold
    inner.export.side_effect = [
        SpanExportResult.FAILURE,
        SpanExportResult.FAILURE,
        SpanExportResult.SUCCESS,   # resets counter
        SpanExportResult.FAILURE,
        SpanExportResult.FAILURE,
        SpanExportResult.FAILURE,   # threshold reached again
    ]
    exporter = _CircuitBreakingExporter(inner, threshold=threshold)

    # 2 failures
    exporter.export([])
    exporter.export([])
    assert exporter._open is False

    # success — counter reset
    exporter.export([])
    assert exporter._failures == 0
    assert exporter._open is False

    # threshold failures again → circuit opens
    for _ in range(threshold):
        exporter.export([])

    assert exporter._open is True


def test_exception_in_wrapped_exporter_counts_as_failure():
    """Exceptions from the wrapped exporter count towards the failure threshold."""
    threshold = 2
    inner = MagicMock()
    inner.export.side_effect = RuntimeError("backend down")
    exporter = _CircuitBreakingExporter(inner, threshold=threshold)

    for _ in range(threshold):
        result = exporter.export([])
        assert result == SpanExportResult.FAILURE

    assert exporter._open is True
