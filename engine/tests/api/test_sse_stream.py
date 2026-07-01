"""Tests for SSE streaming via run_streaming + step events."""
import asyncio
import pytest
from app.llm.base import LLMProvider, LLMResponse, LLMUsage
from tests.pipeline.test_orchestrator import _build_orchestrator, MockLLM


def _usage() -> LLMUsage:
    return LLMUsage(input_tokens=0, output_tokens=0, cost_usd=0.0, model="mock", latency_ms=0)


@pytest.mark.asyncio
async def test_run_streaming_emits_step_events(tmp_path):
    orch = _build_orchestrator(tmp_path)
    queue: asyncio.Queue = asyncio.Queue()
    result = await orch.run_streaming(
        query="show me revenue by store",
        session_id="stream-test",
        step_queue=queue,
    )
    assert result is not None

    events = []
    while not queue.empty():
        events.append(await queue.get())

    step_names = [e["step"] for e in events]
    assert "planning" in step_names
    assert "spl" in step_names
    assert "guardrail" in step_names
    assert "executing" in step_names
    assert "analyzing" in step_names
    assert "deciding" in step_names


@pytest.mark.asyncio
async def test_run_streaming_spl_event_has_spl_field(tmp_path):
    orch = _build_orchestrator(tmp_path)
    queue: asyncio.Queue = asyncio.Queue()
    await orch.run_streaming(
        query="show me revenue by store",
        step_queue=queue,
    )
    events = []
    while not queue.empty():
        events.append(await queue.get())

    spl_events = [e for e in events if e.get("step") == "spl"]
    assert len(spl_events) == 1
    assert "spl" in spl_events[0]


@pytest.mark.asyncio
async def test_run_streaming_result_count_in_analyzing_event(tmp_path):
    orch = _build_orchestrator(tmp_path)
    queue: asyncio.Queue = asyncio.Queue()
    await orch.run_streaming(query="show me revenue by store", step_queue=queue)

    events = []
    while not queue.empty():
        events.append(await queue.get())

    analyzing = next((e for e in events if e.get("step") == "analyzing"), None)
    assert analyzing is not None
    assert "result_count" in analyzing


@pytest.mark.asyncio
async def test_run_streaming_no_queue_still_works(tmp_path):
    """run_streaming with no queue should work exactly like run()."""
    orch = _build_orchestrator(tmp_path)
    result = await orch.run_streaming(
        query="show me revenue by store",
        step_queue=None,
    )
    assert result is not None
    assert result.spl != ""
