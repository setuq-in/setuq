from __future__ import annotations
import logging
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

_tracer_provider: TracerProvider | None = None
_logger = logging.getLogger("setuq.tracer")

_CIRCUIT_BREAK_THRESHOLD = 5


class _CircuitBreakingExporter(SpanExporter):
    """Wraps an exporter and disables it after N consecutive failures."""

    def __init__(self, wrapped: SpanExporter, threshold: int = _CIRCUIT_BREAK_THRESHOLD) -> None:
        self._wrapped = wrapped
        self._threshold = threshold
        self._failures = 0
        self._open = False

    def export(self, spans):
        if self._open:
            return SpanExportResult.SUCCESS
        try:
            result = self._wrapped.export(spans)
            if result == SpanExportResult.SUCCESS:
                self._failures = 0
            else:
                self._failures += 1
                if self._failures >= self._threshold:
                    self._open = True
                    _logger.warning("OTel exporter circuit open after %d failures — spans dropped", self._threshold)
            return result
        except Exception:
            self._failures += 1
            if self._failures >= self._threshold:
                self._open = True
                _logger.warning("OTel exporter circuit open after %d failures — spans dropped", self._threshold)
            return SpanExportResult.FAILURE

    def shutdown(self):
        self._wrapped.shutdown()


def init_tracer(settings) -> TracerProvider | None:
    global _tracer_provider
    if not settings.OBSERVABILITY_ENABLED:
        return None

    resource = Resource.create({
        SERVICE_NAME: "setuq-engine",
        SERVICE_VERSION: "0.1.0",
        "deployment.environment": "development",
    })
    provider = TracerProvider(resource=resource)

    if settings.OTEL_EXPORTER_OTLP_ENDPOINT:
        raw_exporter = OTLPSpanExporter(endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT, insecure=True)
        exporter = _CircuitBreakingExporter(raw_exporter)
        provider.add_span_processor(
            BatchSpanProcessor(
                exporter,
                max_queue_size=2048,
                max_export_batch_size=512,
            )
        )

    trace.set_tracer_provider(provider)
    _tracer_provider = provider
    return provider


def get_tracer(name: str = "setuq") -> trace.Tracer:
    return trace.get_tracer(name)


def shutdown_tracer() -> None:
    global _tracer_provider
    if _tracer_provider:
        _tracer_provider.shutdown()
        _tracer_provider = None
