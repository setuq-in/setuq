import logging
import re
from dataclasses import dataclass
from app.llm.base import LLMProvider
from app.observability.redact import hash_query
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
        # Avoid logging raw SPL at WARNING — it is high-volume and may carry
        # query terms / PII. Hash + length is enough to correlate.
        _logger.debug("spl_generator generated spl hash=%s len=%d", hash_query(spl), len(spl))
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
        cleaned = self._hoist_time_modifiers(cleaned)
        cleaned = self._bound_joins(cleaned)
        if cleaned and not re.search(r"earliest\s*=", cleaned, re.IGNORECASE):
            cleaned = f"earliest=-24h {cleaned}"
        return cleaned

    def _bound_joins(self, spl: str) -> str:
        """Inject `max=` into unbounded `join` stages. A `| join` without a
        `max=`/`overwrite=` option is rejected by the guardrail as unbounded;
        the LLM omits it inconsistently, so bound it deterministically. The
        lookahead mirrors the guardrail regex (checks up to the next `|`)."""
        return re.sub(
            r"(\|\s*join\b)(?![^|]*\b(?:max|overwrite)\s*=)",
            r"\1 max=50000",
            spl,
            flags=re.IGNORECASE,
        )

    def _hoist_time_modifiers(self, spl: str) -> str:
        """Move earliest=/latest= modifiers out of trailing pipe stages into the
        base search. `| earliest=-24h` is not a valid SPL command (there is no
        `earliest` command) and Splunk rejects it with a 400."""
        if "|" not in spl:
            return spl
        segments = spl.split("|")
        hoisted: list[str] = []
        kept: list[str] = []
        for seg in segments[1:]:
            # A pipe stage made up solely of earliest=/latest= tokens is misplaced.
            if re.fullmatch(r"\s*((earliest|latest)\s*=\S+\s*)+", seg, re.IGNORECASE):
                hoisted.append(seg.strip())
            else:
                kept.append(seg.strip())
        if not hoisted:
            return spl
        base = f"{segments[0].rstrip()} {' '.join(hoisted)}".strip()
        return " | ".join([base] + kept)
