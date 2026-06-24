import json
from pathlib import Path
import pytest
from unittest.mock import AsyncMock
from app.pipeline.action_suggester import ActionSuggester, ActionSuggestion
from app.pipeline.analysis_agent import AnalysisAgent
from app.pipeline.audit_logger import AuditLogger
from app.pipeline.decision_engine import DecisionEngine
from app.pipeline.guardrails import QueryGuardrail, GuardrailViolation
from app.pipeline.orchestrator import PipelineOrchestrator, PipelineResult
from app.pipeline.planner import PlannerAgent
from app.pipeline.spl_generator import SPLGenerator
from app.pipeline.splunk_client import SplunkClient
from app.pipeline.summarizer import Summarizer
from app.pipeline.schema_manager import SchemaManager
from app.pipeline.session_manager import SessionManager
from app.llm.base import LLMProvider, LLMResponse, LLMUsage


def _mock_usage() -> LLMUsage:
    return LLMUsage(input_tokens=0, output_tokens=0, cost_usd=0.0, model="mock", latency_ms=0)


class MockLLM(LLMProvider):
    async def generate(self, system_prompt: str, history: list[dict], user_prompt: str) -> LLMResponse:
        if "SPL query generator" in system_prompt:
            return LLMResponse(content='index=chocolate_index sourcetype=sales earliest=-1d | stats sum(revenue) by store_id', usage=_mock_usage())
        if "Explain" in system_prompt:
            return LLMResponse(content="Sums revenue by store from sales in the last day.", usage=_mock_usage())
        if "SOC analyst" in system_prompt:
            return LLMResponse(content='[]', usage=_mock_usage())
        if "investigation planner" in system_prompt:
            return LLMResponse(content='{"needs_plan": false, "steps": [{"description": "Direct query", "spl_hint": null}], "reasoning": "Simple query"}', usage=_mock_usage())
        if "SOC analysis" in system_prompt:
            return LLMResponse(content='{"anomalies": [], "patterns": [], "summary": "No anomalies detected."}', usage=_mock_usage())
        if "decision engine" in system_prompt:
            return LLMResponse(content='{"confidence_score": 0.5, "risk_level": "low", "reasoning": "Routine query.", "recommendation": "suggest", "priority_actions": []}', usage=_mock_usage())
        return LLMResponse(content="Store S001 leads with $50,000 in revenue.", usage=_mock_usage())


_SENTINEL = object()


def _build_orchestrator(tmp_path, llm=None, splunk_return=_SENTINEL, guardrail_indexes=None, chart_inferer=None):
    overrides = tmp_path / "schema_overrides.yaml"
    overrides.write_text("""
indexes:
  chocolate_index:
    sourcetypes:
      sales:
        fields:
          - order_id
          - revenue
          - store_id
""")
    llm = llm or MockLLM()
    schema_manager = SchemaManager(overrides_path=str(overrides))
    spl_generator = SPLGenerator(llm=llm)
    splunk_client = AsyncMock(spec=SplunkClient)
    default_results = [
        {"store_id": "S001", "sum(revenue)": "50000"},
        {"store_id": "S002", "sum(revenue)": "30000"},
    ]
    splunk_client.execute_spl = AsyncMock(return_value=default_results if splunk_return is _SENTINEL else splunk_return)
    summarizer = Summarizer(llm=llm)
    session_manager = SessionManager()
    action_suggester = ActionSuggester(llm=llm)
    guardrail = QueryGuardrail(known_indexes=guardrail_indexes or ["chocolate_index"])
    audit_logger = AuditLogger(log_path=str(tmp_path / "audit.log"))
    planner = PlannerAgent(llm=llm)
    analysis_agent = AnalysisAgent(llm=llm)
    decision_engine = DecisionEngine(llm=llm)

    return PipelineOrchestrator(
        schema_manager=schema_manager,
        spl_generator=spl_generator,
        splunk_client=splunk_client,
        summarizer=summarizer,
        session_manager=session_manager,
        action_suggester=action_suggester,
        guardrail=guardrail,
        audit_logger=audit_logger,
        planner=planner,
        analysis_agent=analysis_agent,
        decision_engine=decision_engine,
        chart_inferer=chart_inferer,
    )


@pytest.fixture
def orchestrator(tmp_path):
    return _build_orchestrator(tmp_path)


@pytest.mark.asyncio
async def test_run_pipeline_returns_complete_result(orchestrator):
    result = await orchestrator.run("Show me total revenue by store")
    assert isinstance(result, PipelineResult)
    assert result.query == "Show me total revenue by store"
    assert "index=chocolate_index" in result.spl
    assert len(result.results) == 2
    assert "S001" in result.summary
    assert result.metadata["result_count"] == 2
    assert "execution_time_ms" in result.metadata
    assert result.session_id
    assert result.spl_explanation
    assert isinstance(result.actions, list)
    # Tier 2 fields
    assert result.plan is not None
    assert result.plan.needs_plan is False
    assert len(result.plan.steps) >= 1
    assert result.analysis is not None
    assert result.analysis.summary
    assert result.decision is not None
    assert result.decision.confidence_score >= 0.0
    assert result.decision.recommendation in ("suggest", "recommend_with_approval", "auto_execute")


@pytest.mark.asyncio
async def test_run_pipeline_preserves_session(orchestrator):
    result1 = await orchestrator.run("first query")
    result2 = await orchestrator.run("second query", session_id=result1.session_id)
    assert result1.session_id == result2.session_id


@pytest.mark.asyncio
async def test_run_pipeline_splunk_error(tmp_path):
    orch = _build_orchestrator(tmp_path, guardrail_indexes=[])
    orch._splunk_client.execute_spl = AsyncMock(side_effect=ConnectionError("Cannot reach Splunk"))

    with pytest.raises(ConnectionError, match="Cannot reach Splunk"):
        await orch.run("test query")


@pytest.mark.asyncio
async def test_run_pipeline_empty_results(tmp_path):
    orch = _build_orchestrator(tmp_path, splunk_return=[])
    result = await orch.run("any revenue?")
    assert result.results == []
    assert result.metadata["result_count"] == 0


@pytest.mark.asyncio
async def test_run_pipeline_audit_log_written(tmp_path):
    orch = _build_orchestrator(tmp_path)
    await orch.run("test audit")
    audit_path = tmp_path / "audit.log"
    content = audit_path.read_text().strip()
    entry = json.loads(content)
    assert entry["query"] == "test audit"
    assert entry["session_id"]
    assert entry["result_count"] == 2


@pytest.mark.asyncio
async def test_run_pipeline_guardrail_violation(tmp_path):
    overrides = tmp_path / "schema_overrides.yaml"
    overrides.write_text("""
indexes:
  main:
    sourcetypes:
      syslog:
        fields: [host, source]
""")

    class BadSPLLLM(LLMProvider):
        async def generate(self, system_prompt: str, history: list[dict], user_prompt: str) -> LLMResponse:
            if "SPL query generator" in system_prompt:
                return LLMResponse(content='index=main sourcetype=syslog | delete', usage=_mock_usage())
            if "Explain" in system_prompt:
                return LLMResponse(content="Deletes all events.", usage=_mock_usage())
            if "investigation planner" in system_prompt:
                return LLMResponse(content='{"needs_plan": false, "steps": [{"description": "x", "spl_hint": null}], "reasoning": "y"}', usage=_mock_usage())
            return LLMResponse(content="[]", usage=_mock_usage())

    orch = _build_orchestrator(tmp_path, llm=BadSPLLLM(), guardrail_indexes=["main"])

    with pytest.raises(GuardrailViolation):
        await orch.run("delete everything")


@pytest.mark.asyncio
async def test_reactive_refresh_triggered_for_all_unknown_indexes(tmp_path):
    """_trigger_reactive_schema_refresh must schedule refresh for EVERY unknown
    index in the violation list, not just the first one."""
    import asyncio
    refreshed = []

    class TrackingSchemaManager(SchemaManager):
        async def refresh_index(self, index_name: str) -> None:
            refreshed.append(index_name)

    overrides = tmp_path / "schema_overrides.yaml"
    overrides.write_text("indexes:\n  known_index:\n    sourcetypes: {}\n")
    schema_manager = TrackingSchemaManager(overrides_path=str(overrides))

    orch = _build_orchestrator(tmp_path)
    orch._schema_manager = schema_manager

    # Simulate two violations referencing different unknown indexes
    violations = [
        "Unknown index 'alpha_index' — not in schema",
        "Unknown index 'beta_index' — not in schema",
    ]
    orch._trigger_reactive_schema_refresh(violations)

    # Allow the scheduled tasks to run
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert "alpha_index" in refreshed, "alpha_index refresh not triggered"
    assert "beta_index" in refreshed, "beta_index refresh not triggered"
