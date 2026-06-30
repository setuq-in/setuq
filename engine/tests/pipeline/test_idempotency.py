"""Sprint 3 — Idempotency cache in PipelineOrchestrator."""
import pytest
from tests.pipeline.test_orchestrator import _build_orchestrator


@pytest.mark.asyncio
async def test_same_query_returns_same_object(tmp_path):
    """Two identical (session_id, query) calls must return the exact same result object."""
    orch = _build_orchestrator(tmp_path)
    orch.configure_idempotency(enabled=True, ttl_seconds=300)

    r1 = await orch.run("Show me total revenue by store", session_id="test-sess")
    r2 = await orch.run("Show me total revenue by store", session_id="test-sess")

    assert r1 is r2, "Expected the idempotency cache to return the same object"


@pytest.mark.asyncio
async def test_different_query_returns_different_object(tmp_path):
    """Different queries in the same session must not share a cached result."""
    orch = _build_orchestrator(tmp_path)
    orch.configure_idempotency(enabled=True, ttl_seconds=300)

    r1 = await orch.run("Show me total revenue by store", session_id="test-sess")
    r2 = await orch.run("Show me login failures by host", session_id="test-sess")

    assert r1 is not r2


@pytest.mark.asyncio
async def test_different_sessions_not_shared(tmp_path):
    """Same query text in different sessions must not be served from the same cache entry."""
    orch = _build_orchestrator(tmp_path)
    orch.configure_idempotency(enabled=True, ttl_seconds=300)

    r1 = await orch.run("Show me total revenue by store", session_id="sess-alpha")
    r2 = await orch.run("Show me total revenue by store", session_id="sess-beta")

    assert r1 is not r2


@pytest.mark.asyncio
async def test_expired_ttl_returns_fresh_result(tmp_path):
    """With ttl_seconds=0 every call is beyond TTL so should produce a fresh result."""
    orch = _build_orchestrator(tmp_path)
    orch.configure_idempotency(enabled=True, ttl_seconds=0)

    r1 = await orch.run("Show me total revenue by store", session_id="test-sess")
    r2 = await orch.run("Show me total revenue by store", session_id="test-sess")

    assert r1 is not r2, "Expected a fresh result after TTL expiry"


@pytest.mark.asyncio
async def test_idempotency_disabled_returns_fresh_result(tmp_path):
    """When idempotency is disabled, every call must execute the pipeline fresh."""
    orch = _build_orchestrator(tmp_path)
    orch.configure_idempotency(enabled=False)

    r1 = await orch.run("Show me total revenue by store", session_id="test-sess")
    r2 = await orch.run("Show me total revenue by store", session_id="test-sess")

    assert r1 is not r2


@pytest.mark.asyncio
async def test_no_session_id_skips_cache(tmp_path):
    """Calls without a session_id should not be cached (no KeyError or incorrect caching)."""
    orch = _build_orchestrator(tmp_path)
    orch.configure_idempotency(enabled=True, ttl_seconds=300)

    r1 = await orch.run("Show me total revenue by store")
    r2 = await orch.run("Show me total revenue by store")

    # Both run the full pipeline; result objects will be different instances
    assert r1 is not r2
    assert r1.query == r2.query
