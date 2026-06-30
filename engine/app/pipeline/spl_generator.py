import logging
import re
from dataclasses import dataclass
from app.llm.base import LLMProvider
from app.pipeline import prompt_registry

_logger = logging.getLogger("setuq.spl_generator")


@dataclass
class SPLResult:
    spl: str
    explanation: str


class SPLGenerator:
    def __init__(self, llm: LLMProvider):
        self._llm = llm

    async def generate_spl(self, query: str, schema_context: str, history: list[dict] | None = None) -> str:
        """Generate and return only the cleaned SPL string (no explain call)."""
        # Literal replace (not str.format) so user-edited prompts can contain
        # JSON/curly-brace examples without raising KeyError.
        system_prompt = prompt_registry.get("spl_generator").replace("{schema_context}", schema_context)
        response = await self._llm.generate(
            system_prompt=system_prompt,
            history=history or [],
            user_prompt=query,
        )
        spl = self._clean_spl(response.content)
        _logger.warning("spl_generator generated spl=%r", spl)
        return spl

    async def explain(self, spl: str) -> str:
        """Explain an SPL query in plain English."""
        response = await self._llm.generate(
            system_prompt=prompt_registry.get("spl_explain"),
            history=[],
            user_prompt=spl,
        )
        return response.content

    async def generate(self, query: str, schema_context: str, history: list[dict] | None = None) -> SPLResult:
        """Generate SPL and explanation. Kept for backward compatibility."""
        spl = await self.generate_spl(query=query, schema_context=schema_context, history=history)
        explanation = await self.explain(spl)
        return SPLResult(spl=spl, explanation=explanation)

    def _clean_spl(self, raw: str) -> str:
        """Strip markdown fences, label prefixes, and whitespace from LLM output."""
        cleaned = raw.strip()
        cleaned = re.sub(r"^```(?:splunk|spl)?\s*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)
        cleaned = cleaned.strip()
        cleaned = re.sub(r"^(SPL|Query|Splunk Query|Search)\s*[:\-]\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.strip()
        if cleaned and not re.search(r"earliest\s*=", cleaned, re.IGNORECASE):
            cleaned = f"earliest=-24h {cleaned}"
        return cleaned
