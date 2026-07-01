import pytest
from app.pipeline.action_suggester import ActionSuggester, ActionSuggestion
from app.llm.base import LLMProvider, LLMResponse, LLMUsage


def _mock_usage() -> LLMUsage:
    return LLMUsage(input_tokens=0, output_tokens=0, cost_usd=0.0, model="mock", latency_ms=0)


class MockLLM(LLMProvider):
    def __init__(self, response: str):
        self._response = response

    async def generate(self, system_prompt: str, history: list[dict], user_prompt: str) -> LLMResponse:
        return LLMResponse(content=self._response, usage=_mock_usage())


@pytest.mark.asyncio
async def test_suggest_parses_valid_json():
    llm = MockLLM(response='[{"action": "block_ip", "target": "10.0.0.1", "reasoning": "Brute force source", "risk_level": "high"}]')
    suggester = ActionSuggester(llm=llm)
    actions = await suggester.suggest(
        query="failed logins", spl="index=security | stats count by src_ip",
        results=[{"src_ip": "10.0.0.1", "count": "500"}], summary="500 failed logins from 10.0.0.1",
    )
    assert len(actions) == 1
    assert actions[0].action == "block_ip"
    assert actions[0].target == "10.0.0.1"
    assert actions[0].risk_level == "high"


@pytest.mark.asyncio
async def test_suggest_returns_empty_on_no_actions():
    llm = MockLLM(response="[]")
    suggester = ActionSuggester(llm=llm)
    actions = await suggester.suggest(
        query="total revenue", spl="index=sales | stats sum(revenue)",
        results=[{"sum(revenue)": "50000"}], summary="Total revenue is $50,000",
    )
    assert actions == []


@pytest.mark.asyncio
async def test_suggest_handles_malformed_json():
    llm = MockLLM(response="not json at all")
    suggester = ActionSuggester(llm=llm)
    actions = await suggester.suggest(
        query="test", spl="index=test", results=[], summary="nothing",
    )
    assert actions == []


@pytest.mark.asyncio
async def test_suggest_strips_markdown_fences():
    llm = MockLLM(response='```json\n[{"action": "escalate", "target": "user123", "reasoning": "Suspicious", "risk_level": "medium"}]\n```')
    suggester = ActionSuggester(llm=llm)
    actions = await suggester.suggest(
        query="test", spl="index=test", results=[], summary="suspicious activity",
    )
    assert len(actions) == 1
    assert actions[0].action == "escalate"


@pytest.mark.asyncio
async def test_suggest_multiple_actions():
    llm = MockLLM(response='[{"action": "block_ip", "target": "1.2.3.4", "reasoning": "r1", "risk_level": "high"}, {"action": "create_alert", "target": "brute_force", "reasoning": "r2", "risk_level": "low"}]')
    suggester = ActionSuggester(llm=llm)
    actions = await suggester.suggest(
        query="test", spl="index=test", results=[], summary="test",
    )
    assert len(actions) == 2
    assert actions[0].action == "block_ip"
    assert actions[1].action == "create_alert"


@pytest.mark.asyncio
async def test_suggest_prompt_includes_context():
    captured = {}

    class CaptureLLM(LLMProvider):
        async def generate(self, system_prompt: str, history: list[dict], user_prompt: str) -> LLMResponse:
            captured["system"] = system_prompt
            captured["user"] = user_prompt
            return LLMResponse(content="[]", usage=_mock_usage())

    suggester = ActionSuggester(llm=CaptureLLM())
    await suggester.suggest(
        query="failed logins", spl="index=security | stats count",
        results=[{"count": "10"}], summary="10 failed logins",
    )
    assert "SOC analyst" in captured["system"]
    assert "failed logins" in captured["user"]
    assert "10 failed logins" in captured["user"]


@pytest.mark.asyncio
async def test_suggest_partial_object_missing_fields():
    """Action objects missing some fields should use defaults."""
    llm = MockLLM(response='[{"action": "block_ip"}]')
    suggester = ActionSuggester(llm=llm)
    actions = await suggester.suggest(
        query="test", spl="index=test", results=[], summary="test",
    )
    assert len(actions) == 1
    assert actions[0].action == "block_ip"
    assert actions[0].target == ""
    assert actions[0].reasoning == ""
    assert actions[0].risk_level == "medium"


@pytest.mark.asyncio
async def test_suggest_non_dict_items_skipped():
    """Non-dict items in array should be silently skipped."""
    llm = MockLLM(response='["string_item", 42, null, {"action": "escalate", "target": "host1", "reasoning": "r", "risk_level": "low"}]')
    suggester = ActionSuggester(llm=llm)
    actions = await suggester.suggest(
        query="test", spl="index=test", results=[], summary="test",
    )
    assert len(actions) == 1
    assert actions[0].action == "escalate"


@pytest.mark.asyncio
async def test_suggest_non_list_json():
    """JSON that is not an array should return empty list."""
    llm = MockLLM(response='{"action": "block_ip", "target": "1.2.3.4"}')
    suggester = ActionSuggester(llm=llm)
    actions = await suggester.suggest(
        query="test", spl="index=test", results=[], summary="test",
    )
    assert actions == []


@pytest.mark.asyncio
async def test_suggest_results_truncated_to_50():
    """Results passed to LLM should be truncated at 50."""
    captured = {}

    class CaptureLLM(LLMProvider):
        async def generate(self, system_prompt: str, history: list[dict], user_prompt: str) -> LLMResponse:
            captured["user"] = user_prompt
            return LLMResponse(content="[]", usage=_mock_usage())

    suggester = ActionSuggester(llm=CaptureLLM())
    big_results = [{"ip": f"10.0.0.{i}"} for i in range(100)]
    await suggester.suggest(
        query="test", spl="index=test", results=big_results, summary="test",
    )
    # Should only contain first 50 results, not all 100
    assert "10.0.0.49" in captured["user"]
    assert "10.0.0.50" not in captured["user"]


@pytest.mark.asyncio
async def test_suggest_empty_results():
    """Empty results should produce 'No results.' in prompt."""
    captured = {}

    class CaptureLLM(LLMProvider):
        async def generate(self, system_prompt: str, history: list[dict], user_prompt: str) -> LLMResponse:
            captured["user"] = user_prompt
            return LLMResponse(content="[]", usage=_mock_usage())

    suggester = ActionSuggester(llm=CaptureLLM())
    await suggester.suggest(
        query="test", spl="index=test", results=[], summary="nothing found",
    )
    assert "No results." in captured["user"]
