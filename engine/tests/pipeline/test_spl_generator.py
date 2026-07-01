import pytest
from app.pipeline.spl_generator import SPLGenerator, SPLResult
from app.llm.base import LLMProvider, LLMResponse, LLMUsage


def _mock_usage() -> LLMUsage:
    return LLMUsage(input_tokens=0, output_tokens=0, cost_usd=0.0, model="mock", latency_ms=0)


class MockLLM(LLMProvider):
    def __init__(self, response: str):
        self._response = response

    async def generate(self, system_prompt: str, history: list[dict], user_prompt: str) -> LLMResponse:
        if "Explain" in system_prompt:
            return LLMResponse(content="This query searches the chocolate_index for sales data and counts events.", usage=_mock_usage())
        return LLMResponse(content=self._response, usage=_mock_usage())


@pytest.fixture
def schema_context():
    return """Available Splunk Schema:

Index: chocolate_index
  Sourcetype: sales
    Fields: order_id, order_date, product_id, store_id, customer_id, quantity, unit_price, discount, revenue, cost, profit
  Sourcetype: products
    Fields: product_id, product_name, brand, category, cocoa_percent, weight_g
  Sourcetype: stores
    Fields: store_id, store_name, city, country, store_type"""


@pytest.mark.asyncio
async def test_generate_returns_spl_result(schema_context):
    mock_llm = MockLLM(response='index=chocolate_index sourcetype=sales | stats sum(revenue) by store_id')
    generator = SPLGenerator(llm=mock_llm)
    result = await generator.generate(query="Show me total sales by store", schema_context=schema_context)
    assert isinstance(result, SPLResult)
    assert result.spl == 'earliest=-24h index=chocolate_index sourcetype=sales | stats sum(revenue) by store_id'
    assert "chocolate_index" in result.explanation


@pytest.mark.asyncio
async def test_generate_strips_markdown_fences(schema_context):
    mock_llm = MockLLM(response='```spl\nindex=chocolate_index sourcetype=sales | stats count\n```')
    generator = SPLGenerator(llm=mock_llm)
    result = await generator.generate(query="Count all sales", schema_context=schema_context)
    assert result.spl == "earliest=-24h index=chocolate_index sourcetype=sales | stats count"


@pytest.mark.asyncio
async def test_system_prompt_includes_schema(schema_context):
    captured_prompts = {}

    class CaptureLLM(LLMProvider):
        async def generate(self, system_prompt: str, history: list[dict], user_prompt: str) -> LLMResponse:
            if "SPL query generator" in system_prompt:
                captured_prompts["system"] = system_prompt
                captured_prompts["user"] = user_prompt
                return LLMResponse(content="index=chocolate_index | stats count", usage=_mock_usage())
            return LLMResponse(content="Explanation of query.", usage=_mock_usage())

    generator = SPLGenerator(llm=CaptureLLM())
    await generator.generate(query="Count events", schema_context=schema_context)
    assert "chocolate_index" in captured_prompts["system"]
    assert "Count events" in captured_prompts["user"]


@pytest.mark.asyncio
async def test_explanation_calls_llm(schema_context):
    explain_called = False

    class TrackLLM(LLMProvider):
        async def generate(self, system_prompt: str, history: list[dict], user_prompt: str) -> LLMResponse:
            nonlocal explain_called
            if "Explain" in system_prompt:
                explain_called = True
                return LLMResponse(content="Explains the query.", usage=_mock_usage())
            return LLMResponse(content="index=chocolate_index | stats count", usage=_mock_usage())

    generator = SPLGenerator(llm=TrackLLM())
    result = await generator.generate(query="Count events", schema_context=schema_context)
    assert explain_called
    assert result.explanation == "Explains the query."


@pytest.mark.asyncio
async def test_empty_llm_response(schema_context):
    mock_llm = MockLLM(response="")
    generator = SPLGenerator(llm=mock_llm)
    result = await generator.generate(query="test", schema_context=schema_context)
    assert result.spl == ""


@pytest.mark.asyncio
async def test_whitespace_only_response(schema_context):
    mock_llm = MockLLM(response="   \n\n  ")
    generator = SPLGenerator(llm=mock_llm)
    result = await generator.generate(query="test", schema_context=schema_context)
    assert result.spl == ""


@pytest.mark.asyncio
async def test_clean_spl_with_splunk_fence(schema_context):
    mock_llm = MockLLM(response='```splunk\nindex=main | stats count\n```')
    generator = SPLGenerator(llm=mock_llm)
    result = await generator.generate(query="test", schema_context=schema_context)
    assert result.spl == "earliest=-24h index=main | stats count"


@pytest.mark.asyncio
async def test_history_passed_to_llm(schema_context):
    captured = {}

    class CaptureLLM(LLMProvider):
        async def generate(self, system_prompt: str, history: list[dict], user_prompt: str) -> LLMResponse:
            if "SPL query generator" in system_prompt:
                captured["history"] = history
                return LLMResponse(content="index=chocolate_index | stats count", usage=_mock_usage())
            return LLMResponse(content="Explanation.", usage=_mock_usage())

    generator = SPLGenerator(llm=CaptureLLM())
    test_history = [{"role": "user", "content": "prior query"}, {"role": "assistant", "content": "prior response"}]
    await generator.generate(query="follow up", schema_context=schema_context, history=test_history)
    assert captured["history"] == test_history


@pytest.mark.asyncio
async def test_clean_spl_strips_label_prefix(schema_context):
    mock_llm = MockLLM(response="SPL: index=chocolate_index sourcetype=sales | stats count")
    generator = SPLGenerator(llm=mock_llm)
    result = await generator.generate(query="test", schema_context=schema_context)
    assert result.spl == "earliest=-24h index=chocolate_index sourcetype=sales | stats count"


@pytest.mark.asyncio
async def test_clean_spl_strips_label_prefix_lowercase(schema_context):
    mock_llm = MockLLM(response="spl: index=chocolate_index | stats count")
    generator = SPLGenerator(llm=mock_llm)
    result = await generator.generate(query="test", schema_context=schema_context)
    assert result.spl == "earliest=-24h index=chocolate_index | stats count"


@pytest.mark.asyncio
async def test_clean_spl_strips_query_prefix(schema_context):
    mock_llm = MockLLM(response="Query: index=main | head 10")
    generator = SPLGenerator(llm=mock_llm)
    result = await generator.generate(query="test", schema_context=schema_context)
    assert result.spl == "earliest=-24h index=main | head 10"


@pytest.mark.asyncio
async def test_clean_spl_strips_label_inside_fence(schema_context):
    mock_llm = MockLLM(response="```spl\nSPL: index=main | stats count\n```")
    generator = SPLGenerator(llm=mock_llm)
    result = await generator.generate(query="test", schema_context=schema_context)
    assert result.spl == "earliest=-24h index=main | stats count"


@pytest.mark.asyncio
async def test_clean_spl_preserves_index_starting_with_s(schema_context):
    """Ensure the label-strip regex doesn't eat valid SPL like 'search index=...'"""
    mock_llm = MockLLM(response="index=splunk_audit | stats count")
    generator = SPLGenerator(llm=mock_llm)
    result = await generator.generate(query="test", schema_context=schema_context)
    assert result.spl == "earliest=-24h index=splunk_audit | stats count"


@pytest.mark.asyncio
async def test_explain_receives_generated_spl(schema_context):
    """Verify the explanation LLM call receives the cleaned SPL, not raw."""
    captured_explain_input = {}

    class TrackLLM(LLMProvider):
        async def generate(self, system_prompt: str, history: list[dict], user_prompt: str) -> LLMResponse:
            if "Explain" in system_prompt:
                captured_explain_input["spl"] = user_prompt
                return LLMResponse(content="Explanation.", usage=_mock_usage())
            return LLMResponse(content="```spl\nindex=main | stats count\n```", usage=_mock_usage())

    generator = SPLGenerator(llm=TrackLLM())
    result = await generator.generate(query="test", schema_context=schema_context)
    # Explanation should get clean SPL, not fenced
    assert captured_explain_input["spl"] == "earliest=-24h index=main | stats count"


@pytest.mark.asyncio
async def test_generate_spl_returns_only_spl_no_explain_call(schema_context):
    """generate_spl() must return the SPL string and must NOT call the explain LLM."""
    explain_calls = []

    class TrackLLM(LLMProvider):
        async def generate(self, system_prompt: str, history: list[dict], user_prompt: str) -> LLMResponse:
            if "Explain" in system_prompt:
                explain_calls.append(True)
                return LLMResponse(content="Should not be called.", usage=_mock_usage())
            return LLMResponse(content="index=main | stats count", usage=_mock_usage())

    generator = SPLGenerator(llm=TrackLLM())
    spl = await generator.generate_spl(query="test", schema_context=schema_context)
    assert spl == "earliest=-24h index=main | stats count"
    assert not explain_calls, "generate_spl() must not invoke the explain LLM"


@pytest.mark.asyncio
async def test_explain_method_returns_explanation(schema_context):
    """explain() must call the LLM with EXPLAIN_PROMPT and return content."""
    class ExplainLLM(LLMProvider):
        async def generate(self, system_prompt: str, history: list[dict], user_prompt: str) -> LLMResponse:
            if "Explain" in system_prompt:
                return LLMResponse(content="Searches main index.", usage=_mock_usage())
            return LLMResponse(content="irrelevant", usage=_mock_usage())

    generator = SPLGenerator(llm=ExplainLLM())
    explanation = await generator.explain("index=main | stats count")
    assert explanation == "Searches main index."


@pytest.mark.asyncio
async def test_clean_spl_injects_earliest_when_missing(schema_context):
    """LLM output without earliest= gets earliest=-24h prepended."""
    class NoTimeLLM(LLMProvider):
        async def generate(self, system_prompt: str, history: list[dict], user_prompt: str) -> LLMResponse:
            return LLMResponse(content="index=security | stats count by user", usage=_mock_usage())

    generator = SPLGenerator(llm=NoTimeLLM())
    spl = await generator.generate_spl(query="failed logins", schema_context=schema_context)
    assert spl.startswith("earliest=-24h")
    assert "index=security" in spl


@pytest.mark.asyncio
async def test_clean_spl_preserves_existing_earliest(schema_context):
    """LLM output that already has earliest= is not modified."""
    class WithTimeLLM(LLMProvider):
        async def generate(self, system_prompt: str, history: list[dict], user_prompt: str) -> LLMResponse:
            return LLMResponse(content="index=security earliest=-7d | stats count by user", usage=_mock_usage())

    generator = SPLGenerator(llm=WithTimeLLM())
    spl = await generator.generate_spl(query="failed logins last 7 days", schema_context=schema_context)
    assert "earliest=-7d" in spl
    assert spl.count("earliest=") == 1
