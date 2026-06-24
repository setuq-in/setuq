"""Sprint 2 — auto_execute gate: recommendation downgrade, header opt-in, error masking."""
import os
import pytest
from unittest.mock import AsyncMock
from httpx import AsyncClient, ASGITransport

# Disable auth for all tests in this module
os.environ.pop("API_KEY", None)

from app.main import app
from app.api.routes import get_orchestrator
from app.pipeline.analysis_agent import AnalysisResult
from app.pipeline.decision_engine import Decision
from app.pipeline.orchestrator import PipelineResult
from app.pipeline.planner import InvestigationPlan


def _make_mock_orchestrator(recommendation: str = "auto_execute") -> AsyncMock:
    """Build a mock PipelineOrchestrator returning a specific recommendation."""
    mock = AsyncMock()
    mock.run = AsyncMock(return_value=PipelineResult(
        query="test query",
        spl="index=myindex earliest=-1d | stats count",
        spl_explanation="counts events",
        results=[],
        summary="No anomalies",
        plan=InvestigationPlan(needs_plan=False, steps=[], reasoning="direct"),
        analysis=AnalysisResult(anomalies=[], patterns=[], summary="clean"),
        decision=Decision(
            confidence_score=0.95,
            risk_level="low",
            reasoning="routine",
            recommendation=recommendation,
            priority_actions=[],
        ),
        actions=[],
        metadata={"result_count": 0, "execution_time_ms": 100},
        session_id="test-session",
    ))
    return mock


# ---------------------------------------------------------------------------
# Gate: auto_execute without header → suggest
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_auto_execute_downgraded_without_header():
    """auto_execute recommendation must become 'suggest' when header is absent."""
    mock_orch = _make_mock_orchestrator("auto_execute")
    app.dependency_overrides[get_orchestrator] = lambda: mock_orch
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/query", json={"query": "test"})
        assert resp.status_code == 200
        assert resp.json()["decision"]["recommendation"] == "suggest", (
            "Expected 'suggest' when X-Allow-Auto-Execute header is absent"
        )
    finally:
        app.dependency_overrides.pop(get_orchestrator, None)


# ---------------------------------------------------------------------------
# Gate: auto_execute WITH header → passes through
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_auto_execute_passes_with_header():
    """auto_execute must pass through unchanged when X-Allow-Auto-Execute: true."""
    mock_orch = _make_mock_orchestrator("auto_execute")
    app.dependency_overrides[get_orchestrator] = lambda: mock_orch
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/query",
                json={"query": "test"},
                headers={"X-Allow-Auto-Execute": "true"},
            )
        assert resp.status_code == 200
        assert resp.json()["decision"]["recommendation"] == "auto_execute", (
            "Expected 'auto_execute' to pass through with the opt-in header"
        )
    finally:
        app.dependency_overrides.pop(get_orchestrator, None)


# ---------------------------------------------------------------------------
# Non-auto_execute recommendations pass through unchanged
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_suggest_recommendation_unchanged():
    """'suggest' recommendation must never be modified by the gate."""
    mock_orch = _make_mock_orchestrator("suggest")
    app.dependency_overrides[get_orchestrator] = lambda: mock_orch
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/query", json={"query": "test"})
        assert resp.status_code == 200
        assert resp.json()["decision"]["recommendation"] == "suggest"
    finally:
        app.dependency_overrides.pop(get_orchestrator, None)


@pytest.mark.asyncio
async def test_review_recommendation_unchanged():
    """'review' recommendation must pass through the gate untouched."""
    mock_orch = _make_mock_orchestrator("review")
    app.dependency_overrides[get_orchestrator] = lambda: mock_orch
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/query", json={"query": "test"})
        assert resp.status_code == 200
        assert resp.json()["decision"]["recommendation"] == "review"
    finally:
        app.dependency_overrides.pop(get_orchestrator, None)


# ---------------------------------------------------------------------------
# Error masking: RuntimeError must not leak internals
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_runtime_error_returns_generic_500():
    """RuntimeError must yield 500 with a generic message — no internal details leaked."""
    mock_orch = AsyncMock()
    mock_orch.run = AsyncMock(
        side_effect=RuntimeError(
            "Internal DB connection string: postgres://user:pass@host/db"
        )
    )
    app.dependency_overrides[get_orchestrator] = lambda: mock_orch
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/query", json={"query": "test"})
        assert resp.status_code == 500
        body = resp.text
        assert "postgres" not in body, "Connection string must not be leaked in error response"
        assert "user:pass" not in body, "Credentials must not be leaked in error response"
        detail = resp.json().get("detail", "")
        assert detail == "Internal server error", (
            f"Expected generic 'Internal server error', got: {detail!r}"
        )
    finally:
        app.dependency_overrides.pop(get_orchestrator, None)
