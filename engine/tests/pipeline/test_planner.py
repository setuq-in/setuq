import pytest
from app.pipeline.planner import PlannerAgent, InvestigationPlan, InvestigationStep
from app.llm.base import LLMProvider, LLMResponse, LLMUsage


def _mock_usage() -> LLMUsage:
    return LLMUsage(input_tokens=0, output_tokens=0, cost_usd=0.0, model="mock", latency_ms=0)


class MockLLM(LLMProvider):
    def __init__(self, response: str):
        self._response = response

    async def generate(self, system_prompt: str, history: list[dict], user_prompt: str) -> LLMResponse:
        return LLMResponse(content=self._response, usage=_mock_usage())


SCHEMA_CONTEXT = "Index: security\n  Sourcetype: syslog\n    Fields: src_ip, dest_ip, action"


@pytest.mark.asyncio
async def test_simple_query_no_plan():
    llm = MockLLM(response='{"needs_plan": false, "steps": [{"description": "Query failed logins", "spl_hint": "index=security action=failure | stats count by src_ip"}], "reasoning": "Simple single-query task"}')
    planner = PlannerAgent(llm=llm)
    plan = await planner.plan("show failed logins", SCHEMA_CONTEXT)
    assert isinstance(plan, InvestigationPlan)
    assert plan.needs_plan is False
    assert len(plan.steps) == 1
    assert "failed logins" in plan.steps[0].description.lower() or "Query" in plan.steps[0].description


@pytest.mark.asyncio
async def test_complex_query_multi_step():
    llm = MockLLM(response='{"needs_plan": true, "steps": [{"description": "Identify brute force sources", "spl_hint": "index=security action=failure | stats count by src_ip | where count > 100"}, {"description": "Check for lateral movement", "spl_hint": "index=security src_ip IN (...) | stats dc(dest_ip) by src_ip"}, {"description": "Timeline the attack", "spl_hint": null}], "reasoning": "Complex investigation requires multiple angles"}')
    planner = PlannerAgent(llm=llm)
    plan = await planner.plan("investigate brute force and lateral movement", SCHEMA_CONTEXT)
    assert plan.needs_plan is True
    assert len(plan.steps) == 3
    assert plan.steps[0].spl_hint is not None
    assert plan.steps[2].spl_hint is None
    assert "multiple" in plan.reasoning.lower()


@pytest.mark.asyncio
async def test_malformed_json_fallback():
    llm = MockLLM(response="this is not json at all")
    planner = PlannerAgent(llm=llm)
    plan = await planner.plan("test", SCHEMA_CONTEXT)
    assert plan.needs_plan is False
    assert len(plan.steps) == 1
    assert "falling back" in plan.reasoning.lower()


@pytest.mark.asyncio
async def test_non_dict_json_fallback():
    llm = MockLLM(response='["not", "a", "dict"]')
    planner = PlannerAgent(llm=llm)
    plan = await planner.plan("test", SCHEMA_CONTEXT)
    assert plan.needs_plan is False
    assert "falling back" in plan.reasoning.lower()


@pytest.mark.asyncio
async def test_empty_steps_gets_default():
    llm = MockLLM(response='{"needs_plan": false, "steps": [], "reasoning": "empty"}')
    planner = PlannerAgent(llm=llm)
    plan = await planner.plan("test", SCHEMA_CONTEXT)
    assert len(plan.steps) == 1
    assert "directly" in plan.steps[0].description.lower()


@pytest.mark.asyncio
async def test_markdown_fences_stripped():
    llm = MockLLM(response='```json\n{"needs_plan": false, "steps": [{"description": "do it", "spl_hint": null}], "reasoning": "simple"}\n```')
    planner = PlannerAgent(llm=llm)
    plan = await planner.plan("test", SCHEMA_CONTEXT)
    assert plan.needs_plan is False
    assert plan.steps[0].description == "do it"


@pytest.mark.asyncio
async def test_prompt_includes_schema():
    captured = {}

    class CaptureLLM(LLMProvider):
        async def generate(self, system_prompt: str, history: list[dict], user_prompt: str) -> LLMResponse:
            captured["system"] = system_prompt
            captured["user"] = user_prompt
            return LLMResponse(content='{"needs_plan": false, "steps": [{"description": "x", "spl_hint": null}], "reasoning": "y"}', usage=_mock_usage())

    planner = PlannerAgent(llm=CaptureLLM())
    await planner.plan("find anomalies", SCHEMA_CONTEXT)
    assert "investigation planner" in captured["system"].lower()
    assert "security" in captured["user"]  # schema context
    assert "find anomalies" in captured["user"]


@pytest.mark.asyncio
async def test_steps_with_non_dict_items_skipped():
    llm = MockLLM(response='{"needs_plan": true, "steps": ["string_step", {"description": "valid step", "spl_hint": null}], "reasoning": "mixed"}')
    planner = PlannerAgent(llm=llm)
    plan = await planner.plan("test", SCHEMA_CONTEXT)
    assert len(plan.steps) == 1
    assert plan.steps[0].description == "valid step"
