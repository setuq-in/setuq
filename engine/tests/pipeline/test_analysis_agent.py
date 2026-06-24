import pytest
from app.pipeline.analysis_agent import AnalysisAgent, AnalysisResult, Anomaly, Pattern
from app.llm.base import LLMProvider, LLMResponse, LLMUsage


def _mock_usage() -> LLMUsage:
    return LLMUsage(input_tokens=0, output_tokens=0, cost_usd=0.0, model="mock", latency_ms=0)


class MockLLM(LLMProvider):
    def __init__(self, response: str):
        self._response = response

    async def generate(self, system_prompt: str, history: list[dict], user_prompt: str) -> LLMResponse:
        return LLMResponse(content=self._response, usage=_mock_usage())


@pytest.mark.asyncio
async def test_analyze_with_anomalies_and_patterns():
    llm = MockLLM(response='{"anomalies": [{"description": "IP 10.0.0.1 has 500 failed logins", "severity": "high", "evidence": "count=500, next highest is 10"}], "patterns": [{"description": "Brute force pattern", "confidence": 0.95, "affected_entities": ["10.0.0.1", "user_admin"]}], "summary": "Clear brute force attack from single IP."}')
    agent = AnalysisAgent(llm=llm)
    result = await agent.analyze(
        query="failed logins", spl="index=security | stats count by src_ip",
        results=[{"src_ip": "10.0.0.1", "count": "500"}],
    )
    assert isinstance(result, AnalysisResult)
    assert len(result.anomalies) == 1
    assert result.anomalies[0].severity == "high"
    assert result.anomalies[0].evidence == "count=500, next highest is 10"
    assert len(result.patterns) == 1
    assert result.patterns[0].confidence == 0.95
    assert "10.0.0.1" in result.patterns[0].affected_entities
    assert "brute force" in result.summary.lower()


@pytest.mark.asyncio
async def test_analyze_empty_results():
    llm = MockLLM(response='{"anomalies": [], "patterns": [], "summary": "No issues found in empty result set."}')
    agent = AnalysisAgent(llm=llm)
    result = await agent.analyze(query="test", spl="index=test", results=[])
    assert result.anomalies == []
    assert result.patterns == []
    assert "no issues" in result.summary.lower()


@pytest.mark.asyncio
async def test_analyze_malformed_json_fallback():
    llm = MockLLM(response="not json")
    agent = AnalysisAgent(llm=llm)
    result = await agent.analyze(query="test", spl="index=test", results=[])
    assert result.anomalies == []
    assert result.patterns == []
    assert "could not be parsed" in result.summary.lower()


@pytest.mark.asyncio
async def test_analyze_non_dict_json_fallback():
    llm = MockLLM(response='[1, 2, 3]')
    agent = AnalysisAgent(llm=llm)
    result = await agent.analyze(query="test", spl="index=test", results=[])
    assert "invalid" in result.summary.lower()


@pytest.mark.asyncio
async def test_analyze_markdown_fences_stripped():
    llm = MockLLM(response='```json\n{"anomalies": [], "patterns": [], "summary": "All clear."}\n```')
    agent = AnalysisAgent(llm=llm)
    result = await agent.analyze(query="test", spl="index=test", results=[])
    assert result.summary == "All clear."


@pytest.mark.asyncio
async def test_analyze_partial_anomaly_defaults():
    llm = MockLLM(response='{"anomalies": [{"description": "spike detected"}], "patterns": [], "summary": "Spike."}')
    agent = AnalysisAgent(llm=llm)
    result = await agent.analyze(query="test", spl="index=test", results=[])
    assert result.anomalies[0].severity == "medium"  # default
    assert result.anomalies[0].evidence == ""  # default


@pytest.mark.asyncio
async def test_analyze_pattern_confidence_float_conversion():
    llm = MockLLM(response='{"anomalies": [], "patterns": [{"description": "p", "confidence": "0.85", "affected_entities": []}], "summary": "x"}')
    agent = AnalysisAgent(llm=llm)
    result = await agent.analyze(query="test", spl="index=test", results=[])
    assert result.patterns[0].confidence == 0.85


@pytest.mark.asyncio
async def test_analyze_results_sampled_with_sketch():
    """Large result sets must be stratified-sampled (k=60) and include a sketch line."""
    captured = {}

    class CaptureLLM(LLMProvider):
        async def generate(self, system_prompt: str, history: list[dict], user_prompt: str) -> LLMResponse:
            captured["user"] = user_prompt
            return LLMResponse(content='{"anomalies": [], "patterns": [], "summary": "ok"}', usage=_mock_usage())

    agent = AnalysisAgent(llm=CaptureLLM())
    big_results = [{"id": str(i)} for i in range(200)]
    await agent.analyze(query="test", spl="index=test", results=big_results)
    # First and last rows always present in stratified sample
    assert '"0"' in captured["user"], "First row missing from sample"
    assert '"199"' in captured["user"], "Last row missing from sample"
    # Sketch line present
    assert "200 total rows" in captured["user"]


@pytest.mark.asyncio
async def test_analyze_prompt_includes_context():
    captured = {}

    class CaptureLLM(LLMProvider):
        async def generate(self, system_prompt: str, history: list[dict], user_prompt: str) -> LLMResponse:
            captured["system"] = system_prompt
            captured["user"] = user_prompt
            return LLMResponse(content='{"anomalies": [], "patterns": [], "summary": "ok"}', usage=_mock_usage())

    agent = AnalysisAgent(llm=CaptureLLM())
    await agent.analyze(query="failed logins", spl="index=security | stats count", results=[{"count": "5"}])
    assert "SOC analysis" in captured["system"]
    assert "failed logins" in captured["user"]
    assert "index=security" in captured["user"]
