"""Relevance gate — heuristic fast paths + LLM tie-break."""
import pytest

from app.llm.base import LLMResponse
from app.pipeline.relevance import (
    RelevanceGate,
    IrrelevantQueryError,
)


class FakeLLM:
    """Records calls and returns a canned JSON body."""

    def __init__(self, content: str = '{"relevant": true, "reason": "ok"}'):
        self.content = content
        self.calls = 0

    async def generate(self, system_prompt, history, user_prompt):
        self.calls += 1
        return LLMResponse(content=self.content)


class FakeSchemaManager:
    def __init__(self, schema: dict):
        self._schema = schema

    def get_schema(self) -> dict:
        return self._schema


_SCHEMA = {
    "indexes": {
        "security": {
            "sourcetypes": {
                "wineventlog": {"fields": [{"name": "EventCode"}, "src_ip"]},
            }
        }
    }
}


@pytest.mark.asyncio
async def test_soc_vocab_fast_accept_no_llm():
    llm = FakeLLM()
    gate = RelevanceGate(llm=llm, schema_manager=FakeSchemaManager(_SCHEMA))

    result = await gate.check("show failed logins in the last 24h")

    assert result.relevant is True
    assert result.method == "heuristic"
    assert llm.calls == 0


@pytest.mark.asyncio
async def test_schema_term_fast_accept_no_llm():
    llm = FakeLLM()
    gate = RelevanceGate(llm=llm, schema_manager=FakeSchemaManager(_SCHEMA))

    # "wineventlog" is a sourcetype name — should match via schema terms.
    result = await gate.check("anything from wineventlog please")

    assert result.relevant is True
    assert llm.calls == 0


@pytest.mark.asyncio
async def test_junk_fast_reject_no_llm():
    llm = FakeLLM()
    gate = RelevanceGate(llm=llm, schema_manager=FakeSchemaManager(_SCHEMA))

    with pytest.raises(IrrelevantQueryError):
        await gate.check("write me a poem about the ocean")

    assert llm.calls == 0  # free reject — no model call


@pytest.mark.asyncio
async def test_ambiguous_escalates_to_llm_accept():
    llm = FakeLLM('{"relevant": true, "reason": "operational metric"}')
    gate = RelevanceGate(llm=llm, schema_manager=FakeSchemaManager(_SCHEMA))

    # No SOC vocab, no junk pattern — must ask the LLM.
    result = await gate.check("how many widgets shipped on tuesday")

    assert result.relevant is True
    assert result.method == "llm"
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_ambiguous_llm_rejects():
    llm = FakeLLM('{"relevant": false, "reason": "general knowledge"}')
    gate = RelevanceGate(llm=llm, schema_manager=FakeSchemaManager(_SCHEMA))

    with pytest.raises(IrrelevantQueryError, match="general knowledge"):
        await gate.check("how many widgets shipped on tuesday")

    assert llm.calls == 1


@pytest.mark.asyncio
async def test_llm_failure_fails_open():
    # Invalid JSON triggers LLMOutputValidationError after retries -> fail open.
    llm = FakeLLM("not json at all")
    gate = RelevanceGate(llm=llm, schema_manager=FakeSchemaManager(_SCHEMA))

    result = await gate.check("how many widgets shipped on tuesday")

    assert result.relevant is True
    assert result.method == "llm_error"


@pytest.mark.asyncio
async def test_disabled_gate_always_passes():
    llm = FakeLLM()
    gate = RelevanceGate(llm=llm, schema_manager=FakeSchemaManager(_SCHEMA), enabled=False)

    result = await gate.check("write me a poem")

    assert result.relevant is True
    assert result.method == "disabled"
    assert llm.calls == 0
