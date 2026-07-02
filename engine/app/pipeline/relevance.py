"""Relevance gate — reject off-topic queries before any agent workflow runs.

Hybrid strategy (cheapest path first):

1. **Free fast-accept** — if the query shares any term with the SOC/Splunk
   vocabulary or the live schema (index/sourcetype/field names), treat it as
   on-topic. No LLM call.
2. **Free fast-reject** — if the query has zero schema/SOC overlap AND matches an
   obvious off-topic pattern (greetings, creative writing, weather, …), reject
   without an LLM call.
3. **LLM tie-break** — only genuinely ambiguous queries (no overlap, no obvious
   junk) cost one classification call. The classifier fails *open* (lets the
   query through) so a flaky model never wrongly blocks real SOC work.

On rejection the gate raises ``IrrelevantQueryError`` so the orchestrator can
audit + emit a friendly message and skip planning/SPL/execution entirely.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from pydantic import BaseModel
from opentelemetry import metrics as otel_metrics

from app.pipeline import prompt_registry
from app.pipeline.llm_utils import generate_validated, LLMOutputValidationError

_logger = logging.getLogger("setuq.relevance")
_meter = otel_metrics.get_meter("setuq.relevance")
_rejected_counter = _meter.create_counter(
    "relevance.rejected",
    description="Off-topic queries rejected by the relevance gate",
)
_llm_check_counter = _meter.create_counter(
    "relevance.llm_checks",
    description="Ambiguous queries escalated to LLM relevance classification",
)


# User-facing copy shown when a query is rejected. Single source of truth so the
# REST and SSE paths stay in sync.
NOT_APPLICABLE_MESSAGE = (
    "I can only answer questions about your Splunk/SOC data — logins, alerts, "
    "indexes, events, traffic, and so on. Try asking something like "
    "\"show failed logins in the last 24h\" or \"top source IPs by event count\"."
)


class IrrelevantQueryError(Exception):
    """Raised when a user query is not answerable from Splunk/SOC data."""

    def __init__(self, reason: str, category: str = "not_applicable"):
        self.reason = reason
        self.category = category
        super().__init__(reason)


# Strong SOC/Splunk vocabulary — presence strongly implies an on-topic query.
_SOC_VOCAB = {
    "splunk", "spl", "index", "indexes", "sourcetype", "sourcetypes", "search",
    "query", "log", "logs", "event", "events", "login", "logins", "logon",
    "auth", "authentication", "failed", "failure", "failures", "error", "errors",
    "alert", "alerts", "ip", "ips", "user", "users", "host", "hosts", "source",
    "dest", "destination", "firewall", "threat", "threats", "anomaly",
    "anomalies", "incident", "breach", "attack", "attacks", "malware",
    "endpoint", "traffic", "port", "ports", "dns", "http", "https", "url",
    "session", "sessions", "audit", "siem", "soc", "correlation", "detection",
    "vulnerability", "exfiltration", "brute", "bruteforce", "lateral", "count",
    "stats", "timechart", "dashboard", "field", "fields", "status", "bytes",
    "latency", "request", "requests", "response", "blocked", "denied", "allowed",
}

# Obvious off-topic patterns. Used ONLY on the free fast-reject path, and only
# when the query also has zero schema/SOC overlap — so a security question that
# happens to say "weather" (e.g. "weather-service login errors") still passes.
_JUNK_PATTERNS = [
    r"\bwrite\s+(me\s+)?(a|an)\s+(poem|song|story|essay|joke|haiku|rap|limerick)\b",
    r"\b(recipe|cook|bake|ingredient|cooking)\b",
    r"\bweather\b",
    r"\btell\s+me\s+a\s+joke\b",
    r"\bwho\s+(is|was|were)\s+(the\s+)?(president|king|queen|ceo|prime\s+minister)\b",
    r"\btranslate\b.*\b(to|into)\s+\w+",
    r"\b(meaning\s+of\s+life|capital\s+of\s+\w+)\b",
    r"^\s*(hi|hello|hey|yo|sup|thanks|thank\s+you|good\s+(morning|evening|night))\W*$",
    r"\bwhat\s+(can|do)\s+you\s+do\b",
    r"\b(stock\s+price|sports\s+score|movie|football|cricket|basketball)\b",
]

_WORD_RE = re.compile(r"[a-z0-9_]+")


SYSTEM_PROMPT = """You are a relevance classifier for a Splunk SOC (Security Operations Center) assistant.

Given a user's question and a summary of the available Splunk data, decide whether the question can plausibly be answered by running an SPL search over that data.

Return relevant=true when the question is about logs, events, security, operations, metrics, users, hosts, network traffic, alerts, or anything answerable from Splunk data.
Return relevant=false when the question is small talk, a greeting, general knowledge, creative writing, coding help, math, or otherwise unrelated to the Splunk data.

When unsure, prefer relevant=true.

Return ONLY a JSON object: {"relevant": boolean, "reason": short string}. No markdown fences."""

prompt_registry.register("relevance_gate", SYSTEM_PROMPT)


class _RelevanceSchema(BaseModel):
    relevant: bool = True
    reason: str = ""


@dataclass
class RelevanceResult:
    relevant: bool
    reason: str
    method: str  # "heuristic" | "llm" | "llm_error" | "disabled"


class RelevanceGate:
    def __init__(self, llm, schema_manager=None, enabled: bool = True):
        self._llm = llm
        self._schema_manager = schema_manager
        self._enabled = enabled
        self._junk = [re.compile(p, re.IGNORECASE) for p in _JUNK_PATTERNS]

    def _schema_terms(self) -> set[str]:
        """Collect index/sourcetype/field names from the live schema as a lower-cased set."""
        if self._schema_manager is None:
            return set()
        terms: set[str] = set()
        schema = self._schema_manager.get_schema()
        for idx_name, idx in schema.get("indexes", {}).items():
            terms.add(str(idx_name).lower())
            for st_name, st in idx.get("sourcetypes", {}).items():
                terms.add(str(st_name).lower())
                for f in st.get("fields", []):
                    name = f.get("name") if isinstance(f, dict) else f
                    if name:
                        terms.add(str(name).lower())
        return terms

    def _available_summary(self) -> str:
        """Compact list of available indexes/sourcetypes for the LLM tie-break prompt."""
        if self._schema_manager is None:
            return "No schema available."
        schema = self._schema_manager.get_schema()
        indexes = list(schema.get("indexes", {}).keys())
        sourcetypes: list[str] = []
        for idx in schema.get("indexes", {}).values():
            sourcetypes.extend(idx.get("sourcetypes", {}).keys())
        return (
            f"Available indexes: {', '.join(indexes) or 'none'}\n"
            f"Available sourcetypes: {', '.join(sorted(set(sourcetypes))) or 'none'}"
        )

    async def check(self, query: str) -> RelevanceResult:
        """Classify a query. Returns RelevanceResult if on-topic; raises IrrelevantQueryError if not."""
        if not self._enabled:
            return RelevanceResult(True, "relevance gate disabled", "disabled")

        tokens = set(_WORD_RE.findall(query.lower()))
        vocab = _SOC_VOCAB | self._schema_terms()
        if tokens & vocab:
            return RelevanceResult(True, "matched SOC/schema vocabulary", "heuristic")

        if any(p.search(query) for p in self._junk):
            _rejected_counter.add(1, {"method": "heuristic"})
            _logger.info("relevance.rejected method=heuristic")
            raise IrrelevantQueryError(
                "Query appears unrelated to Splunk/SOC data.", category="not_applicable"
            )

        # Ambiguous — no vocab overlap, no obvious junk. Pay for one LLM call.
        _llm_check_counter.add(1)
        user_prompt = f"User question: {query}\n\n{self._available_summary()}"
        try:
            data = await generate_validated(
                llm=self._llm,
                system_prompt=prompt_registry.resolve("relevance_gate", SYSTEM_PROMPT),
                history=[],
                user_prompt=user_prompt,
                model_class=_RelevanceSchema,
            )
        except LLMOutputValidationError as exc:
            # Fail open — never block real work because the classifier misbehaved.
            _logger.warning("relevance classifier failed, failing open: %s", exc)
            return RelevanceResult(True, "classifier unavailable — failed open", "llm_error")

        if not data.relevant:
            _rejected_counter.add(1, {"method": "llm"})
            _logger.info("relevance.rejected method=llm reason=%s", data.reason)
            raise IrrelevantQueryError(
                data.reason or "Query not related to Splunk/SOC data.",
                category="not_applicable",
            )
        return RelevanceResult(True, data.reason or "classified relevant", "llm")
