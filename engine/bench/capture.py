from __future__ import annotations
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

_exporter: InMemorySpanExporter | None = None
_installed = False


class StageCapture:
    def __enter__(self) -> "StageCapture":
        global _exporter, _installed
        if not _installed:
            provider = TracerProvider()
            _exporter = InMemorySpanExporter()
            provider.add_span_processor(SimpleSpanProcessor(_exporter))
            trace.set_tracer_provider(provider)
            _installed = True
        assert _exporter is not None
        _exporter.clear()
        return self

    def __exit__(self, *exc) -> None:
        global _exporter, _installed
        # Reset OTel's set-once global tracer provider so capture doesn't leak
        # spans into this module's exporter for the rest of the test suite,
        # and doesn't depend on test ordering (mirrors
        # tests/observability/test_trace_llm.py's _reset_tracer_provider).
        trace._TRACER_PROVIDER_SET_ONCE._done = False
        trace._TRACER_PROVIDER = None
        _exporter = None
        _installed = False
        return None

    def clear(self) -> None:
        if _exporter is not None:
            _exporter.clear()

    def durations_ms(self) -> dict[str, float]:
        out: dict[str, float] = {}
        if _exporter is None:
            return out
        for span in _exporter.get_finished_spans():
            out[span.name] = (span.end_time - span.start_time) / 1_000_000.0
        return out
