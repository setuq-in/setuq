import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry import trace
from app.observability.decorators import trace_llm
from app.llm.base import LLMResponse, LLMUsage


class _FakeProvider:
    """Fake LLM provider that returns a known LLMResponse."""

    @trace_llm(provider="fake")
    async def generate(self, system_prompt: str, history: list, user_prompt: str) -> LLMResponse:
        return LLMResponse(
            content="test response",
            usage=LLMUsage(
                input_tokens=10,
                output_tokens=5,
                cost_usd=0.001,
                model="fake-model",
                latency_ms=100,
            ),
        )


def _reset_tracer_provider():
    """Force-reset OTel's global tracer provider so tests are isolated."""
    import opentelemetry.trace as _trace
    # _TRACER_PROVIDER_SET_ONCE is a Once() guard; reset its internal flag.
    _trace._TRACER_PROVIDER_SET_ONCE._done = False
    _trace._TRACER_PROVIDER = None


@pytest.fixture
def span_exporter():
    _reset_tracer_provider()
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    yield exporter
    exporter.clear()
    _reset_tracer_provider()


@pytest.mark.asyncio
async def test_trace_llm_creates_span(span_exporter):
    provider = _FakeProvider()
    result = await provider.generate("sys", [], "user")
    assert result.content == "test response"

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "llm.fake.generate"


@pytest.mark.asyncio
async def test_trace_llm_records_genai_attrs(span_exporter):
    provider = _FakeProvider()
    await provider.generate("sys", [], "user")

    spans = span_exporter.get_finished_spans()
    attrs = spans[0].attributes
    assert attrs.get("gen_ai.system") == "fake"
    assert attrs.get("gen_ai.usage.input_tokens") == 10
    assert attrs.get("gen_ai.usage.output_tokens") == 5
    assert attrs.get("gen_ai.usage.cost_usd") == 0.001
    assert attrs.get("gen_ai.request.model") == "fake-model"
