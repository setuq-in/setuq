import os
import pytest
from unittest.mock import AsyncMock
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.api.routes import get_orchestrator, get_schema_manager
from app.pipeline.action_suggester import ActionSuggestion
from app.pipeline.analysis_agent import AnalysisResult, Anomaly, Pattern
from app.pipeline.decision_engine import Decision
from app.pipeline.orchestrator import PipelineResult
from app.pipeline.guardrails import GuardrailViolation
from app.pipeline.planner import InvestigationPlan, InvestigationStep
from app.pipeline.schema_manager import SchemaManager

# Auth is disabled in tests: API_KEY env var is empty (default), so bearer token validation is skipped.
# Tests call protected endpoints without Authorization headers and expect them to pass.
os.environ.pop("API_KEY", None)


@pytest.fixture
def mock_orchestrator():
    orch = AsyncMock()
    orch.run = AsyncMock(return_value=PipelineResult(
        query="test query",
        spl="index=chocolate_index | stats count",
        spl_explanation="Counts all events in chocolate_index.",
        results=[{"count": "100"}],
        summary="There are 100 events.",
        plan=InvestigationPlan(
            needs_plan=False,
            steps=[InvestigationStep(description="Direct query", spl_hint=None)],
            reasoning="Simple query",
        ),
        analysis=AnalysisResult(
            anomalies=[Anomaly(description="Spike detected", severity="medium", evidence="count=100")],
            patterns=[],
            summary="Minor spike in event count.",
        ),
        decision=Decision(
            confidence_score=0.6,
            risk_level="low",
            reasoning="Low confidence, routine data.",
            recommendation="suggest",
            priority_actions=["investigate_further"],
        ),
        actions=[
            ActionSuggestion(
                action="investigate_further",
                target="chocolate_index",
                reasoning="Unusual event count",
                risk_level="low",
            )
        ],
        metadata={"result_count": 1, "execution_time_ms": 150},
        session_id="test-session-id",
    ))
    return orch


@pytest.fixture
def mock_schema_manager(tmp_path):
    overrides = tmp_path / "overrides.yaml"
    overrides.write_text("""
indexes:
  chocolate_index:
    sourcetypes:
      sales:
        fields: [order_id, revenue]
""")
    return SchemaManager(overrides_path=str(overrides))


@pytest.fixture
def client(mock_orchestrator, mock_schema_manager):
    app.dependency_overrides[get_orchestrator] = lambda: mock_orchestrator
    app.dependency_overrides[get_schema_manager] = lambda: mock_schema_manager
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_post_query(client, mock_orchestrator):
    async with client as c:
        response = await c.post("/api/query", json={"query": "test query"})
    assert response.status_code == 200
    data = response.json()
    assert data["query"] == "test query"
    assert data["spl"] == "index=chocolate_index | stats count"
    assert data["spl_explanation"] == "Counts all events in chocolate_index."
    assert data["summary"] == "There are 100 events."
    assert data["session_id"] == "test-session-id"
    # Tier 2 fields
    assert data["plan"]["needs_plan"] is False
    assert len(data["plan"]["steps"]) == 1
    assert data["analysis"]["summary"] == "Minor spike in event count."
    assert len(data["analysis"]["anomalies"]) == 1
    assert data["analysis"]["anomalies"][0]["severity"] == "medium"
    assert data["decision"]["confidence_score"] == 0.6
    assert data["decision"]["recommendation"] == "suggest"
    assert data["decision"]["priority_actions"] == ["investigate_further"]
    assert len(data["actions"]) == 1


@pytest.mark.asyncio
async def test_post_query_empty_body(client):
    async with client as c:
        response = await c.post("/api/query", json={})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_post_query_guardrail_violation(client, mock_orchestrator):
    mock_orchestrator.run = AsyncMock(side_effect=GuardrailViolation("No time range specified"))
    async with client as c:
        response = await c.post("/api/query", json={"query": "bad query"})
    assert response.status_code == 422
    assert "Guardrail violation" in response.json()["detail"]


@pytest.mark.asyncio
async def test_post_query_connection_error(client, mock_orchestrator):
    mock_orchestrator.run = AsyncMock(side_effect=ConnectionError("Cannot reach Splunk"))
    async with client as c:
        response = await c.post("/api/query", json={"query": "test"})
    assert response.status_code == 502


@pytest.mark.asyncio
async def test_post_query_runtime_error(client, mock_orchestrator):
    mock_orchestrator.run = AsyncMock(side_effect=RuntimeError("Splunk search job failed"))
    async with client as c:
        response = await c.post("/api/query", json={"query": "test"})
    assert response.status_code == 500


@pytest.mark.asyncio
async def test_post_schema_refresh(client):
    async with client as c:
        response = await c.post("/api/schema/refresh")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "refreshed"


@pytest.mark.asyncio
async def test_get_schema(client):
    async with client as c:
        response = await c.get("/api/schema")
    assert response.status_code == 200
    assert "chocolate_index" in response.json()["indexes"]


@pytest.mark.asyncio
async def test_get_health(client):
    async with client as c:
        response = await c.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_sse_stream_respects_session_rate_limit(client, mock_orchestrator):
    """The /api/query/stream endpoint must enforce per-session rate limit.
    Without the fix, session_rate_limit is never checked and the request goes through (200).
    With the fix, a denied session receives 429 before the stream starts."""
    from unittest.mock import patch
    from app.pipeline.action_suggester import ActionSuggestion
    from app.pipeline.planner import InvestigationPlan, InvestigationStep
    from app.pipeline.analysis_agent import AnalysisResult
    from app.pipeline.decision_engine import Decision

    sid = "test-session-sse"

    # Wire run_streaming so the SSE endpoint can stream normally when not rate-limited
    mock_orchestrator.run_streaming = AsyncMock(return_value=PipelineResult(
        query="q", spl="index=main | stats count", spl_explanation="counts",
        results=[], summary="none",
        plan=InvestigationPlan(needs_plan=False,
                               steps=[InvestigationStep(description="x", spl_hint=None)],
                               reasoning=""),
        analysis=AnalysisResult(anomalies=[], patterns=[], summary="ok"),
        decision=Decision(confidence_score=0.5, risk_level="low",
                          reasoning="", recommendation="suggest", priority_actions=[]),
        actions=[], metadata={"result_count": 0, "execution_time_ms": 10},
        session_id=sid,
    ))

    async def _deny(session_id, limit):
        return False

    async with client as c:
        with patch("app.api.routes.check_session_rate_limit", side_effect=_deny):
            response = await c.get(
                "/api/query/stream",
                params={"query": "show me all events", "session_id": sid},
            )
    assert response.status_code == 429, (
        f"Expected 429 when session rate limit exhausted, got {response.status_code}"
    )
