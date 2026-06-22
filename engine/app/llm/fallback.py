from __future__ import annotations
import logging
from app.llm.base import LLMProvider, LLMResponse
from app.llm.harness import BudgetExceeded

_logger = logging.getLogger("setuq.llm.fallback")


class FallbackProvider(LLMProvider):
    """Try providers in order. On any exception, log warning and try next."""

    def __init__(self, providers: list[LLMProvider]) -> None:
        if not providers:
            raise ValueError("FallbackProvider requires at least one provider")
        self._providers = providers

    async def generate(
        self,
        system_prompt: str,
        history: list[dict],
        user_prompt: str,
    ) -> LLMResponse:
        last_exc: Exception | None = None
        for i, provider in enumerate(self._providers):
            try:
                return await provider.generate(
                    system_prompt=system_prompt,
                    history=history,
                    user_prompt=user_prompt,
                )
            except BudgetExceeded:
                raise  # never fall through budget violations — cost guarantee must hold
            except Exception as exc:
                last_exc = exc
                _logger.warning(
                    "LLM provider %d/%d failed (%s: %s) — trying next",
                    i + 1, len(self._providers),
                    type(exc).__name__, exc,
                )
        raise RuntimeError(
            f"All {len(self._providers)} LLM providers failed"
        ) from last_exc
