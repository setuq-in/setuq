from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class LLMUsage:
    input_tokens: int
    output_tokens: int
    cost_usd: float
    model: str
    latency_ms: int


@dataclass
class LLMResponse:
    content: str
    usage: LLMUsage | None = None


class LLMProvider(ABC):
    @abstractmethod
    async def generate(
        self,
        system_prompt: str,
        history: list[dict],
        user_prompt: str,
    ) -> LLMResponse:
        """Generate a response given system prompt, conversation history, and user prompt.

        history: list of {"role": "user"|"assistant", "content": str} dicts,
                 ordered oldest-first. Empty list for first turn.
        """
        pass
