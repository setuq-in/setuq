"""Sprint 3 — generate_validated: retry logic and LLMOutputValidationError."""
import json
import pytest
from pydantic import BaseModel

from app.llm.base import LLMProvider, LLMResponse, LLMUsage
from app.pipeline.llm_utils import generate_validated, LLMOutputValidationError


# ---------------------------------------------------------------------------
# Simple Pydantic model used in all tests
# ---------------------------------------------------------------------------

class _M(BaseModel):
    value: int


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _usage() -> LLMUsage:
    return LLMUsage(input_tokens=0, output_tokens=0, cost_usd=0.0, model="mock", latency_ms=0)


def _resp(content: str) -> LLMResponse:
    return LLMResponse(content=content, usage=_usage())


# ---------------------------------------------------------------------------
# Mock LLMs
# ---------------------------------------------------------------------------

class _FixedLLM(LLMProvider):
    """Returns a pre-configured sequence of responses, then repeats the last."""

    def __init__(self, *responses: str) -> None:
        self._responses = list(responses)
        self._index = 0

    async def generate(self, system_prompt: str, history: list[dict], user_prompt: str) -> LLMResponse:
        content = self._responses[min(self._index, len(self._responses) - 1)]
        self._index += 1
        return _resp(content)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_valid_json_no_retry():
    """Valid JSON on first call must return validated model without any retry."""
    llm = _FixedLLM('{"value": 42}')
    result = await generate_validated(llm, "sys", [], "user", _M, max_retries=1)
    assert isinstance(result, _M)
    assert result.value == 42
    # Only one generate() call should have been made
    assert llm._index == 1


@pytest.mark.asyncio
async def test_invalid_then_valid_json_retries_once(caplog):
    """Invalid JSON on first attempt, valid on second → one warning logged, correct result."""
    import logging
    llm = _FixedLLM("not-valid-json", '{"value": 7}')

    with caplog.at_level(logging.WARNING, logger="setuq.llm_utils"):
        result = await generate_validated(llm, "sys", [], "user", _M, max_retries=1)

    assert isinstance(result, _M)
    assert result.value == 7
    assert llm._index == 2  # two calls made
    # Warning must have been emitted on the first failed attempt
    assert any("validation failed" in r.message.lower() for r in caplog.records)


@pytest.mark.asyncio
async def test_invalid_json_both_attempts_raises():
    """Invalid JSON on all attempts must raise LLMOutputValidationError."""
    llm = _FixedLLM("bad", "also-bad")

    with pytest.raises(LLMOutputValidationError):
        await generate_validated(llm, "sys", [], "user", _M, max_retries=1)

    assert llm._index == 2


@pytest.mark.asyncio
async def test_valid_json_schema_mismatch_retries_then_raises():
    """JSON that parses but fails Pydantic validation should retry and raise on second fail."""
    # {"value": "not-an-int"} is valid JSON but fails _M validation (value must be int)
    llm = _FixedLLM('{"value": "not-an-int"}', '{"value": "still-wrong"}')

    with pytest.raises(LLMOutputValidationError):
        await generate_validated(llm, "sys", [], "user", _M, max_retries=1)

    assert llm._index == 2


@pytest.mark.asyncio
async def test_valid_json_schema_mismatch_then_valid_succeeds():
    """Pydantic failure on first attempt, valid on second → returns correct model."""
    llm = _FixedLLM('{"value": "wrong"}', '{"value": 99}')

    result = await generate_validated(llm, "sys", [], "user", _M, max_retries=1)
    assert isinstance(result, _M)
    assert result.value == 99
    assert llm._index == 2


@pytest.mark.asyncio
async def test_max_retries_zero_no_retry():
    """With max_retries=0, a single failed call must raise immediately without retrying."""
    llm = _FixedLLM("invalid")

    with pytest.raises(LLMOutputValidationError):
        await generate_validated(llm, "sys", [], "user", _M, max_retries=0)

    assert llm._index == 1  # exactly one attempt


@pytest.mark.asyncio
async def test_markdown_fenced_json_is_accepted():
    """Markdown-fenced JSON output should be cleaned and validated successfully."""
    llm = _FixedLLM("```json\n{\"value\": 5}\n```")

    result = await generate_validated(llm, "sys", [], "user", _M, max_retries=1)
    assert result.value == 5


@pytest.mark.asyncio
async def test_prose_before_fence_is_stripped():
    """LLM may emit prose before the JSON fence; clean_llm_json must still extract the JSON."""
    llm = _FixedLLM("Sure, here's the JSON:\n```json\n{\"value\": 12}\n```")

    result = await generate_validated(llm, "sys", [], "user", _M, max_retries=0)
    assert result.value == 12


@pytest.mark.asyncio
async def test_thinking_preamble_before_json_is_stripped():
    """<thinking> block followed by raw JSON must be parsed correctly."""
    raw = "<thinking>Let me think...</thinking>\n{\"value\": 77}"
    llm = _FixedLLM(raw)

    result = await generate_validated(llm, "sys", [], "user", _M, max_retries=0)
    assert result.value == 77


@pytest.mark.asyncio
async def test_unfenced_json_with_leading_text_is_extracted():
    """Raw JSON preceded by a label line must be found and parsed."""
    llm = _FixedLLM("Here is the result:\n{\"value\": 55}")

    result = await generate_validated(llm, "sys", [], "user", _M, max_retries=0)
    assert result.value == 55
