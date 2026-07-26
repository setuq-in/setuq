from __future__ import annotations
import asyncio
from app.llm.base import LLMProvider, LLMResponse
from tests.pipeline.test_orchestrator import MockLLM


class LatencyMockLLM(MockLLM):
    def __init__(self, delay_ms: int = 200) -> None:
        self._delay = delay_ms / 1000.0

    async def generate(self, system_prompt: str, history: list[dict], user_prompt: str) -> LLMResponse:
        await asyncio.sleep(self._delay)
        return await super().generate(system_prompt, history, user_prompt)


class FaultyMockLLM(LLMProvider):
    def __init__(self, mode: str) -> None:
        assert mode in ("timeout", "error")
        self._mode = mode

    async def generate(self, system_prompt: str, history: list[dict], user_prompt: str) -> LLMResponse:
        if self._mode == "timeout":
            await asyncio.sleep(10.0)
            return LLMResponse(content="", usage=None)
        raise RuntimeError("injected LLM failure")
