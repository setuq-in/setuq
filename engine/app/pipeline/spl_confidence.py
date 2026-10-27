import logging

from pydantic import BaseModel

from app.llm.base import LLMProvider
from app.pipeline import prompt_registry
from app.pipeline.llm_utils import generate_validated, LLMOutputValidationError

_logger = logging.getLogger("setuq.spl_confidence")


class _ConfidenceSchema(BaseModel):
    confidence: float = 0.0
    reasoning: str = ""


class SPLConfidenceScorer:
    """Rates how well a generated SPL answers the user's NL question (0.0-1.0)."""

    def __init__(self, llm: LLMProvider):
        self._llm = llm

    async def score(self, query: str, spl: str, schema_context: str) -> float | None:
        """Return a clamped 0.0-1.0 confidence, or None if scoring failed."""
        user_prompt = (
            f"User question: {query}\n\n"
            f"Generated SPL: {spl}\n\n"
            f"Schema context:\n{schema_context}"
        )
        try:
            data = await generate_validated(
                llm=self._llm,
                system_prompt=prompt_registry.get("spl_confidence"),
                history=[],
                user_prompt=user_prompt,
                model_class=_ConfidenceSchema,
            )
        except LLMOutputValidationError:
            _logger.warning("spl_confidence scoring failed — returning None")
            return None
        return max(0.0, min(1.0, data.confidence))
