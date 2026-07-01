import pytest
from app.pipeline.decision_engine import DecisionEngine, Decision
from app.llm.base import LLMProvider, LLMResponse, LLMUsage


def _mock_usage() -> LLMUsage:
    return LLMUsage(input_tokens=0, output_tokens=0, cost_usd=0.0, model="mock", latency_ms=0)


class MockLLM(LLMProvider):
    def __init__(self, response: str):
        self._response = response

    async def generate(self, system_prompt: str, history: list[dict], user_prompt: str) -> LLMResponse:
        return LLMResponse(content=self._response, usage=_mock_usage())


DECIDE_ARGS = dict(
    query="failed logins",
    summary="500 failed logins from 10.0.0.1",
    analysis_summary="Clear brute force pattern",
    anomaly_count=1,
    pattern_count=1,
    actions_suggested=[{"action": "block_ip", "target": "10.0.0.1", "risk_level": "high"}],
)


@pytest.mark.asyncio
async def test_high_confidence_decision():
    llm = MockLLM(response='{"confidence_score": 0.92, "risk_level": "high", "reasoning": "Clear brute force with high event count.", "recommendation": "recommend_with_approval", "priority_actions": ["block_ip 10.0.0.1", "reset passwords"]}')
    engine = DecisionEngine(llm=llm)
    decision = await engine.decide(**DECIDE_ARGS)
    assert isinstance(decision, Decision)
    assert decision.confidence_score == 0.92
    assert decision.risk_level == "high"
    assert decision.recommendation == "recommend_with_approval"
    assert len(decision.priority_actions) == 2


@pytest.mark.asyncio
async def test_low_confidence_suggest():
    llm = MockLLM(response='{"confidence_score": 0.4, "risk_level": "low", "reasoning": "Ambiguous data.", "recommendation": "suggest", "priority_actions": ["investigate_further"]}')
    engine = DecisionEngine(llm=llm)
    decision = await engine.decide(**DECIDE_ARGS)
    assert decision.confidence_score == 0.4
    assert decision.recommendation == "suggest"


@pytest.mark.asyncio
async def test_auto_execute_high_confidence_low_risk():
    llm = MockLLM(response='{"confidence_score": 0.95, "risk_level": "low", "reasoning": "Very clear benign pattern.", "recommendation": "auto_execute", "priority_actions": ["whitelist"]}')
    engine = DecisionEngine(llm=llm)
    decision = await engine.decide(**DECIDE_ARGS)
    assert decision.recommendation == "auto_execute"


@pytest.mark.asyncio
async def test_threshold_enforced_when_llm_returns_invalid_recommendation():
    """If LLM returns bogus recommendation, engine computes it from thresholds."""
    llm = MockLLM(response='{"confidence_score": 0.5, "risk_level": "medium", "reasoning": "test", "recommendation": "yolo", "priority_actions": []}')
    engine = DecisionEngine(llm=llm)
    decision = await engine.decide(**DECIDE_ARGS)
    assert decision.recommendation == "suggest"  # 0.5 < 0.7 -> suggest


@pytest.mark.asyncio
async def test_threshold_enforced_empty_recommendation():
    llm = MockLLM(response='{"confidence_score": 0.85, "risk_level": "high", "reasoning": "test", "recommendation": "", "priority_actions": []}')
    engine = DecisionEngine(llm=llm)
    decision = await engine.decide(**DECIDE_ARGS)
    assert decision.recommendation == "recommend_with_approval"  # 0.7-0.9 + not low


@pytest.mark.asyncio
async def test_malformed_json_fallback():
    llm = MockLLM(response="not json")
    engine = DecisionEngine(llm=llm)
    decision = await engine.decide(**DECIDE_ARGS)
    assert decision.confidence_score == 0.0
    assert decision.recommendation == "suggest"
    assert "could not be parsed" in decision.reasoning.lower()


@pytest.mark.asyncio
async def test_non_dict_json_fallback():
    llm = MockLLM(response='[1, 2]')
    engine = DecisionEngine(llm=llm)
    decision = await engine.decide(**DECIDE_ARGS)
    assert decision.confidence_score == 0.0
    assert "invalid" in decision.reasoning.lower()


@pytest.mark.asyncio
async def test_markdown_fences_stripped():
    llm = MockLLM(response='```json\n{"confidence_score": 0.8, "risk_level": "medium", "reasoning": "ok", "recommendation": "recommend_with_approval", "priority_actions": []}\n```')
    engine = DecisionEngine(llm=llm)
    decision = await engine.decide(**DECIDE_ARGS)
    assert decision.confidence_score == 0.8


@pytest.mark.asyncio
async def test_prompt_includes_context():
    captured = {}

    class CaptureLLM(LLMProvider):
        async def generate(self, system_prompt: str, history: list[dict], user_prompt: str) -> LLMResponse:
            captured["system"] = system_prompt
            captured["user"] = user_prompt
            return LLMResponse(content='{"confidence_score": 0.5, "risk_level": "low", "reasoning": "x", "recommendation": "suggest", "priority_actions": []}', usage=_mock_usage())

    engine = DecisionEngine(llm=CaptureLLM())
    await engine.decide(**DECIDE_ARGS)
    assert "decision engine" in captured["system"].lower()
    assert "failed logins" in captured["user"]
    assert "block_ip" in captured["user"]
    assert "Anomalies detected: 1" in captured["user"]


# --- _compute_recommendation unit tests ---

def test_compute_low_confidence():
    assert DecisionEngine._compute_recommendation(0.3, "high") == "suggest"
    assert DecisionEngine._compute_recommendation(0.69, "low") == "suggest"


def test_compute_mid_confidence():
    assert DecisionEngine._compute_recommendation(0.7, "high") == "recommend_with_approval"
    assert DecisionEngine._compute_recommendation(0.85, "medium") == "recommend_with_approval"
    assert DecisionEngine._compute_recommendation(0.9, "low") == "recommend_with_approval"


def test_compute_high_confidence_low_risk():
    assert DecisionEngine._compute_recommendation(0.91, "low") == "auto_execute"
    assert DecisionEngine._compute_recommendation(0.99, "low") == "auto_execute"


def test_compute_high_confidence_high_risk_no_auto():
    """Even >0.9, if risk is not low, should not auto-execute."""
    assert DecisionEngine._compute_recommendation(0.95, "high") == "recommend_with_approval"
    assert DecisionEngine._compute_recommendation(0.95, "critical") == "recommend_with_approval"
