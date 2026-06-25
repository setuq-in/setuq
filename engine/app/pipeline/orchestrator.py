import asyncio
import hashlib
import logging
import re as _re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from opentelemetry import trace as otel_trace
from app.observability import get_tracer, hash_query
from app.llm.harness import init_run_budget, reset_run_budget, BudgetExceeded, get_run_budget_usage
from app.pipeline.action_suggester import ActionSuggester, ActionSuggestion
from app.pipeline.analysis_agent import AnalysisAgent, AnalysisResult
from app.pipeline.audit_logger import AuditLogger, AuditEntry
from app.pipeline.decision_engine import DecisionEngine, Decision
from app.pipeline.guardrails import QueryGuardrail, GuardrailViolation
from app.pipeline.planner import PlannerAgent, InvestigationPlan
from app.pipeline.relevance import RelevanceGate, IrrelevantQueryError, NOT_APPLICABLE_MESSAGE
from app.pipeline.schema_manager import SchemaManager
from app.pipeline.session_manager import SessionManager, ConversationTurn
from app.pipeline.spl_generator import SPLGenerator
from app.pipeline.splunk_client import SplunkClient
from app.pipeline.summarizer import Summarizer

logger = logging.getLogger(__name__)


class _TTLCache:
    """Bounded LRU cache with per-entry TTL. Not thread-safe beyond asyncio."""

    def __init__(self, maxsize: int, ttl: float) -> None:
        self._data: OrderedDict = OrderedDict()
        self._maxsize = maxsize
        self._ttl = ttl

    def get(self, key):
        item = self._data.get(key)
        if item is None:
            return None
        value, ts = item
        if time.time() - ts > self._ttl:
            del self._data[key]
            return None
        self._data.move_to_end(key)
        return value

    def set(self, key, value) -> None:
        if key in self._data:
            self._data.move_to_end(key)
        self._data[key] = (value, time.time())
        while len(self._data) > self._maxsize:
            self._data.popitem(last=False)

    def delete(self, key) -> None:
        self._data.pop(key, None)


@dataclass
class PipelineResult:
    query: str
    spl: str
    spl_explanation: str
    results: list[dict]
    summary: str
    plan: InvestigationPlan
    analysis: AnalysisResult
    decision: Decision
    actions: list[ActionSuggestion]
    metadata: dict
    session_id: str


class PipelineOrchestrator:
    def __init__(
        self,
        schema_manager: SchemaManager,
        spl_generator: SPLGenerator,
        splunk_client: SplunkClient,
        summarizer: Summarizer,
        session_manager: SessionManager,
        action_suggester: ActionSuggester,
        guardrail: QueryGuardrail,
        audit_logger: AuditLogger,
        planner: PlannerAgent,
        analysis_agent: AnalysisAgent,
        decision_engine: DecisionEngine,
        relevance_gate: RelevanceGate | None = None,
    ):
        self._schema_manager = schema_manager
        self._relevance_gate = relevance_gate
        self._spl_generator = spl_generator
        self._splunk_client = splunk_client
        self._summarizer = summarizer
        self._session_manager = session_manager
        self._action_suggester = action_suggester
        self._guardrail = guardrail
        self._audit_logger = audit_logger
        self._planner = planner
        self._analysis_agent = analysis_agent
        self._decision_engine = decision_engine
        self._idem_cache: _TTLCache = _TTLCache(maxsize=2048, ttl=300)
        self._idem_enabled: bool = True
        self._inflight_refreshes: set[str] = set()

    def _trigger_reactive_schema_refresh(self, violations: list[str]) -> None:
        """Fire-and-forget background schema refresh; de-duped by in-flight set."""
        loop = asyncio.get_running_loop()
        found_index = False
        seen: set[str] = set()
        for v in violations:
            m = _re.search(r"Unknown index '([^']+)'", v)
            if not m:
                continue
            found_index = True
            index_name = m.group(1)
            if index_name in seen or index_name in self._inflight_refreshes:
                continue
            seen.add(index_name)
            self._inflight_refreshes.add(index_name)

            async def _run_index(idx: str = index_name) -> None:
                try:
                    await self._schema_manager.refresh_index(idx)
                finally:
                    self._inflight_refreshes.discard(idx)

            loop.create_task(_run_index())

        if found_index:
            return

        # No specific index — full refresh (keyed on sentinel "")
        if "" in self._inflight_refreshes:
            return
        self._inflight_refreshes.add("")

        async def _run_full() -> None:
            try:
                await self._schema_manager.refresh()
            finally:
                self._inflight_refreshes.discard("")

        loop.create_task(_run_full())

    def _audit_rejection(self, session_id: str, query: str, reason: str, start_time: float) -> None:
        """Persist an off-topic rejection to the audit log (no SPL was run)."""
        elapsed_ms = int((time.time() - start_time) * 1000)
        ctx = otel_trace.get_current_span().get_span_context()
        trace_id = format(ctx.trace_id, "032x") if ctx.is_valid else ""
        self._audit_logger.log(AuditEntry(
            timestamp=time.time(),
            session_id=session_id,
            query=query,
            spl="",
            result_count=0,
            execution_time_ms=elapsed_ms,
            rejected=True,
            rejection_reason=reason,
            trace_id=trace_id,
        ))

    def configure_idempotency(self, enabled: bool = True, ttl_seconds: int = 300) -> None:
        self._idem_enabled = enabled
        self._idem_cache = _TTLCache(maxsize=2048, ttl=ttl_seconds)

    def _idem_key(self, session_id: str, query: str, history_len: int) -> str:
        h = hashlib.sha256()
        h.update(session_id.encode())
        h.update(b"\x00")
        h.update(query.encode())
        h.update(b"\x00")
        h.update(str(history_len).encode())
        return h.hexdigest()

    async def run(self, query: str, session_id: str | None = None) -> PipelineResult:
        """Run the full pipeline: plan -> NL->SPL -> guardrails -> execute -> summarize -> analyze -> decide -> actions."""
        idem_key: str | None = None
        if self._idem_enabled and session_id:
            history_pre = await self._session_manager.build_history_messages(session_id)
            idem_key = self._idem_key(session_id, query, len(history_pre))
            cached = self._idem_cache.get(idem_key)
            if cached is not None:
                return cached

        budget_token = init_run_budget(
            max_tokens=50_000,
            max_cost_usd=0.50,
        )
        try:
            result = await self._run_inner(query=query, session_id=session_id, on_step=None)
        finally:
            reset_run_budget(budget_token)

        if idem_key is not None:
            self._idem_cache.set(idem_key, result)

        return result

    async def run_streaming(
        self,
        query: str,
        session_id: str | None = None,
        step_queue: "asyncio.Queue | None" = None,
    ) -> PipelineResult:
        """Like run() but emits step events to step_queue for SSE consumers."""
        async def _on_step(step: str, data: dict) -> None:
            if step_queue is not None:
                await step_queue.put({"step": step, **data})

        budget_token = init_run_budget(max_tokens=50_000, max_cost_usd=0.50)
        try:
            result = await self._run_inner(
                query=query, session_id=session_id, on_step=_on_step
            )
        finally:
            reset_run_budget(budget_token)
        return result

    async def _run_inner(
        self,
        query: str,
        session_id: str | None = None,
        on_step=None,
    ) -> PipelineResult:
        """Inner run body; budget context is already initialized by run()."""
        start_time = time.time()
        guardrail_violations: list[str] = []

        tracer = get_tracer()
        with tracer.start_as_current_span("pipeline.run") as span:
            span.set_attribute("session_id", session_id or "")
            span.set_attribute("query.hash", hash_query(query))
            from app.pipeline.prompt_registry import all_versions as _prompt_versions
            for pname, phash in _prompt_versions().items():
                span.set_attribute(f"prompt.version.{pname}", phash)

            # Load or create session
            session_id, _ = await self._session_manager.get_or_create(session_id)
            history = await self._session_manager.build_history_messages(session_id)

            async def _emit(step: str, data: dict | None = None) -> None:
                if on_step is not None:
                    try:
                        await on_step(step, data or {})
                    except Exception:
                        pass

            # Step 0: Relevance gate — reject off-topic queries before any agent
            # work (planning / SPL / execution). Off-topic queries never trigger
            # the workflow; the caller gets a friendly not-applicable message.
            if self._relevance_gate is not None:
                await _emit("checking_relevance")
                with tracer.start_as_current_span("pipeline.relevance") as rel_span:
                    try:
                        rel = await self._relevance_gate.check(query)
                        rel_span.set_attribute("relevance.method", rel.method)
                    except IrrelevantQueryError as e:
                        rel_span.set_status(otel_trace.StatusCode.ERROR, e.reason)
                        rel_span.set_attribute("relevance.rejected", True)
                        self._audit_rejection(session_id, query, e.reason, start_time)
                        await _emit("not_applicable", {"message": NOT_APPLICABLE_MESSAGE, "reason": e.reason})
                        raise

            # Step 1: Get schema context
            with tracer.start_as_current_span("pipeline.get_schema"):
                schema_context = self._schema_manager.get_prompt_context()

            await _emit("planning")
            # Step 2+3: Plan and generate SPL concurrently (plan doesn't feed into SPL generation)
            # Note: generate_spl only — explain is deferred to run concurrent with execute_spl below.
            with tracer.start_as_current_span("pipeline.plan_and_generate"):
                try:
                    async with asyncio.TaskGroup() as tg:
                        plan_t = tg.create_task(
                            self._planner.plan(query=query, schema_context=schema_context)
                        )
                        spl_t = tg.create_task(
                            self._spl_generator.generate_spl(query=query, schema_context=schema_context, history=history)
                        )
                except* Exception as eg:
                    for _e in eg.exceptions:
                        if not isinstance(_e, asyncio.CancelledError):
                            raise _e from eg
                    raise eg.exceptions[0]
                plan = plan_t.result()
                from app.pipeline.spl_generator import SPLResult as _SPLResult
                spl_result = _SPLResult(spl=spl_t.result(), explanation="")

            await _emit("spl", {"spl": spl_result.spl, "explanation": spl_result.explanation})

            await _emit("guardrail")
            # Step 4: Validate against guardrails
            with tracer.start_as_current_span("pipeline.guardrail") as guardrail_span:
                try:
                    self._guardrail.validate(spl_result.spl)
                except GuardrailViolation as e:
                    guardrail_violations = e.reason.split("; ")
                    logger.warning(
                        "Guardrail violation for query=%r spl=%r reason=%s",
                        query, spl_result.spl, e.reason,
                    )
                    guardrail_span.set_status(otel_trace.StatusCode.ERROR, e.reason)
                    guardrail_span.set_attribute("guardrail.violations", ", ".join(guardrail_violations))
                    self._trigger_reactive_schema_refresh(guardrail_violations)
                    raise

            await _emit("executing")
            # Step 5: Execute Splunk + generate explanation concurrently
            # (explain is pure LLM, explain is independent of results)
            with tracer.start_as_current_span("pipeline.execute_and_explain"):
                try:
                    async with asyncio.TaskGroup() as tg:
                        execute_t = tg.create_task(
                            self._splunk_client.execute_spl(spl_result.spl)
                        )
                        explain_t = tg.create_task(
                            self._spl_generator.explain(spl_result.spl)
                        )
                except* Exception as eg:
                    for _e in eg.exceptions:
                        if not isinstance(_e, asyncio.CancelledError):
                            raise _e from eg
                    raise eg.exceptions[0]
                results = execute_t.result()
                from app.pipeline.spl_generator import SPLResult as _SPLResult
                spl_result = _SPLResult(spl=spl_result.spl, explanation=explain_t.result())

            await _emit("analyzing", {"result_count": len(results)})
            # Step 6+7: Summarize and analyze concurrently (both only need query/spl/results)
            with tracer.start_as_current_span("pipeline.summarize_analyze"):
                try:
                    async with asyncio.TaskGroup() as tg:
                        sum_t = tg.create_task(
                            self._summarizer.summarize(query=query, spl=spl_result.spl, results=results, history=history)
                        )
                        ana_t = tg.create_task(
                            self._analysis_agent.analyze(query=query, spl=spl_result.spl, results=results)
                        )
                except* Exception as eg:
                    for _e in eg.exceptions:
                        if not isinstance(_e, asyncio.CancelledError):
                            raise _e from eg
                    raise eg.exceptions[0]
                summary, analysis = sum_t.result(), ana_t.result()

            await _emit("deciding")
            # Step 8: Suggest actions (needs summary)
            with tracer.start_as_current_span("pipeline.suggest_actions"):
                actions = await self._action_suggester.suggest(
                    query=query, spl=spl_result.spl, results=results, summary=summary
                )

            # Step 9: Decision engine (needs summary + analysis + actions)
            with tracer.start_as_current_span("pipeline.decide"):
                decision = await self._decision_engine.decide(
                    query=query,
                    summary=summary,
                    analysis_summary=analysis.summary,
                    anomaly_count=len(analysis.anomalies),
                    pattern_count=len(analysis.patterns),
                    actions_suggested=[
                        {"action": a.action, "target": a.target, "risk_level": a.risk_level}
                        for a in actions
                    ],
                )

            # Step 10: Save turn to session
            await self._session_manager.append_turn(
                session_id,
                ConversationTurn(query=query, spl=spl_result.spl, result_count=len(results), summary=summary),
            )

            elapsed_ms = int((time.time() - start_time) * 1000)

            # Capture trace_id from current span context
            ctx = otel_trace.get_current_span().get_span_context()
            trace_id = format(ctx.trace_id, '032x') if ctx.is_valid else ""

            # Step 11: Audit log
            total_tokens, total_cost_usd = get_run_budget_usage()
            self._audit_logger.log(AuditEntry(
                timestamp=time.time(),
                session_id=session_id,
                query=query,
                spl=spl_result.spl,
                result_count=len(results),
                execution_time_ms=elapsed_ms,
                spl_explanation=spl_result.explanation,
                actions_suggested=[
                    {"action": a.action, "target": a.target, "risk_level": a.risk_level}
                    for a in actions
                ],
                guardrail_violations=guardrail_violations,
                trace_id=trace_id,
                total_tokens=total_tokens,
                total_cost_usd=total_cost_usd,
            ))

            return PipelineResult(
                query=query,
                spl=spl_result.spl,
                spl_explanation=spl_result.explanation,
                results=results,
                summary=summary,
                plan=plan,
                analysis=analysis,
                decision=decision,
                actions=actions,
                metadata={
                    "result_count": len(results),
                    "execution_time_ms": elapsed_ms,
                },
                session_id=session_id,
            )
