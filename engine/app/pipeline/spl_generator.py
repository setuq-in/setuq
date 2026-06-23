import logging
import re
from dataclasses import dataclass
from app.llm.base import LLMProvider
from app.pipeline import prompt_registry

_logger = logging.getLogger("setuq.spl_generator")


SYSTEM_PROMPT_HEAD = """You are a Splunk SPL query generator. Given a natural language question and a schema of available Splunk indexes, sourcetypes, and fields, generate an accurate SPL query."""

SYSTEM_PROMPT_TAIL = """Rules:
- Output ONLY the raw SPL query, nothing else
- Do NOT wrap in markdown code fences
- Do NOT prefix with labels like "SPL:", "Query:", or "Search:"
- Always specify the index and sourcetype
- Only use fields that exist in the provided schema
- Use appropriate SPL commands (stats, timechart, where, eval, etc.)
- ALWAYS include an `earliest=` time modifier in the generated SPL — every query must be time-bounded
- If the question specifies a relative time window (e.g., "last 7 days", "past 24 hours"), translate it directly: "last N days" → `earliest=-Nd`, "last N hours" → `earliest=-Nh`
- If no time range is specified in the question, default to `earliest=-24h`
- For period-over-period comparisons (e.g., "compare last 7 days to previous 7 days"), do NOT use `join`. Use `earliest=-14d` then `eval period=if(_time>=relative_time(now(),"-7d"), "current", "previous") | stats ... by period` instead. `join` is banned by guardrails.
- If `join` is unavoidable, always include `max=` (e.g., `join max=10000 ...`)"""

# Kept for prompt registry (content unchanged)
SYSTEM_PROMPT = f"{SYSTEM_PROMPT_HEAD}\n\n{{schema_context}}\n\n{SYSTEM_PROMPT_TAIL}"

EXPLAIN_PROMPT = """You are a Splunk SPL expert. Explain the following SPL query in plain English so a junior analyst can understand it.

Rules:
- Be concise — 2-4 sentences
- Explain what data is being searched and what operations are performed
- Mention the index, sourcetype, and key commands used
- Do NOT suggest improvements, just explain what it does"""


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
        system_prompt = prompt_registry.resolve("spl_generator", SYSTEM_PROMPT).replace("{schema_context}", schema_context)
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
            system_prompt=prompt_registry.resolve("spl_explain", EXPLAIN_PROMPT),
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

prompt_registry.register("spl_generator", SYSTEM_PROMPT)
prompt_registry.register("spl_explain", EXPLAIN_PROMPT)
