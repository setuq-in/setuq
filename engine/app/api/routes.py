import asyncio
import json
import logging
import os
import secrets
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Security
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.api.rate_limiter import limiter, check_session_rate_limit
from app.api.schemas import (
    QueryRequest, QueryResponse, QueryMetadata, ActionSuggestionSchema,
    InvestigationStepSchema, PlanSchema, AnomalySchema, PatternSchema,
    AnalysisSchema, DecisionSchema, HealthResponse,
    SplunkChartExport, ChartExportRequest,
    ChartFromSessionRequest, ChartFromSessionResponse,
)
from app.llm.base import LLMProvider
from app.pipeline.guardrails import GuardrailViolation
from app.pipeline.relevance import IrrelevantQueryError, NOT_APPLICABLE_MESSAGE
from app.pipeline.orchestrator import PipelineOrchestrator
from app.pipeline.splunk_chart_export import build_exports
from app.pipeline.schema_manager import SchemaManager

router = APIRouter(prefix="/api")

_bearer = HTTPBearer(auto_error=False)

_logger = logging.getLogger("setuq.api")


def _get_api_key() -> str:
    # Re-reads Settings (env + .env) at call time so tests can override, and so
    # this sees exactly what _validate_startup_config's fail-closed check saw —
    # a .env-only API_KEY must not silently disable auth here (split-brain).
    from app.config import Settings

    return Settings().API_KEY


def _ip_rate_limit() -> str:
    """Per-IP slowapi limit string, read from env each request (config-driven)."""
    return os.environ.get("RATE_LIMIT_PER_IP", "60/minute")


def _session_rate_limit() -> int:
    """Per-session request cap parsed from RATE_LIMIT_PER_SESSION (e.g. '10/minute')."""
    raw = os.environ.get("RATE_LIMIT_PER_SESSION", "10/minute")
    try:
        return int(raw.split("/", 1)[0])
    except (ValueError, IndexError):
        return 10


async def _enforce_session_rate_limit(session_id: str | None) -> None:
    """Raise 429 when the session's per-minute cap is exceeded. No-op without a session."""
    if not session_id:
        return
    if not await check_session_rate_limit(session_id, limit=_session_rate_limit()):
        raise HTTPException(
            status_code=429,
            detail="Session rate limit exceeded",
            headers={"Retry-After": "60"},
        )


def verify_api_key(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> None:
    api_key = _get_api_key()
    if not api_key:
        return  # auth disabled in dev mode
    # Constant-time compare — avoid leaking key length/prefix via timing.
    if credentials is None or not secrets.compare_digest(
        credentials.credentials, api_key
    ):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def get_orchestrator():
    """Overridden at app startup."""
    raise RuntimeError("Orchestrator not initialized")


def get_schema_manager():
    """Overridden at app startup."""
    raise RuntimeError("Schema manager not initialized")


def get_llm_provider():
    """Overridden at app startup."""
    raise RuntimeError("LLM provider not initialized")


@router.post("/chart/export", response_model=SplunkChartExport)
@limiter.limit(_ip_rate_limit)
async def chart_export(
    body: ChartExportRequest,
    request: Request,
    _: None = Depends(verify_api_key),
):
    """Convert a chart spec + SPL into Splunk Simple XML and Studio JSON source."""
    return build_exports(body.spl, body.chart_spec)


@router.post("/chart/from-session", response_model=ChartFromSessionResponse)
@limiter.limit("60/minute")
async def chart_from_session(
    body: ChartFromSessionRequest,
    request: Request,
    orchestrator: PipelineOrchestrator = Depends(get_orchestrator),
    _: None = Depends(verify_api_key),
):
    """Re-chart a session's last results without a new data query.

    "chart it" / "make that a pie" flow. `chart_type` may name one or more
    types ("pie", "pie and bar"); omit for best-fit. 404 if nothing cached.
    """
    specs = await orchestrator.rechart(body.session_id, body.chart_type)
    if not specs:
        raise HTTPException(
            status_code=404,
            detail="No cached results for this session — run a data query first.",
        )
    return ChartFromSessionResponse(chart_spec=specs[0], chart_specs=specs)


@router.post("/query", response_model=QueryResponse)
@limiter.limit(_ip_rate_limit)
async def query(
    body: QueryRequest,
    request: Request,
    orchestrator: PipelineOrchestrator = Depends(get_orchestrator),
    _: None = Depends(verify_api_key),
):
    try:
        await _enforce_session_rate_limit(body.session_id)

        task = asyncio.ensure_future(
            orchestrator.run(body.query, session_id=body.session_id)
        )

        async def _watch_disconnect() -> None:
            while not task.done():
                if await request.is_disconnected():
                    task.cancel()
                    return
                await asyncio.sleep(0.5)

        watcher = asyncio.ensure_future(_watch_disconnect())
        try:
            result = await task
        except asyncio.CancelledError:
            watcher.cancel()
            raise HTTPException(status_code=499, detail="Client disconnected")
        finally:
            watcher.cancel()

        # Gate auto_execute — only allow if client explicitly opts in via header
        recommendation = result.decision.recommendation
        if recommendation == "auto_execute":
            allow = request.headers.get("X-Allow-Auto-Execute", "").lower()
            if allow != "true":
                recommendation = "suggest"
                _logger.warning(
                    "auto_execute recommendation downgraded to suggest — "
                    "client did not send X-Allow-Auto-Execute: true"
                )

        return QueryResponse(
            query=result.query,
            spl=result.spl,
            spl_explanation=result.spl_explanation,
            results=result.results,
            summary=result.summary,
            plan=PlanSchema(
                needs_plan=result.plan.needs_plan,
                steps=[
                    InvestigationStepSchema(description=s.description, spl_hint=s.spl_hint)
                    for s in result.plan.steps
                ],
                reasoning=result.plan.reasoning,
            ),
            analysis=AnalysisSchema(
                anomalies=[
                    AnomalySchema(description=a.description, severity=a.severity, evidence=a.evidence)
                    for a in result.analysis.anomalies
                ],
                patterns=[
                    PatternSchema(
                        description=p.description,
                        confidence=p.confidence,
                        affected_entities=p.affected_entities,
                    )
                    for p in result.analysis.patterns
                ],
                summary=result.analysis.summary,
            ),
            decision=DecisionSchema(
                confidence_score=result.decision.confidence_score,
                risk_level=result.decision.risk_level,
                reasoning=result.decision.reasoning,
                recommendation=recommendation,
                priority_actions=result.decision.priority_actions,
            ),
            actions=[
                ActionSuggestionSchema(
                    action=a.action,
                    target=a.target,
                    reasoning=a.reasoning,
                    risk_level=a.risk_level,
                )
                for a in result.actions
            ],
            metadata=QueryMetadata(**result.metadata),
            session_id=result.session_id,
            chart_spec=result.chart_spec,
            chart_specs=result.chart_specs,
        )
    except IrrelevantQueryError as e:
        # Off-topic query — the agent workflow never ran. Return 200 with a
        # friendly message so the UI shows a chat reply, not an error toast.
        return JSONResponse(
            status_code=200,
            content={
                "status": "not_applicable",
                "message": NOT_APPLICABLE_MESSAGE,
                "reason": e.reason,
                "query": body.query,
                "session_id": body.session_id or "",
            },
        )
    except GuardrailViolation as e:
        raise HTTPException(status_code=422, detail=f"Guardrail violation: {e.reason}")
    except ConnectionError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except HTTPException:
        # Re-raise 429 (session rate limit) / 499 (client disconnect) raised
        # above instead of letting the bare except below rewrite them to 500.
        raise
    except Exception as e:
        _logger.exception("Unhandled error in /query: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/schema")
async def get_schema(
    schema_manager: SchemaManager = Depends(get_schema_manager),
    _: None = Depends(verify_api_key),
):
    return schema_manager.get_schema()


@router.post("/schema/refresh")
async def refresh_schema(
    schema_manager: SchemaManager = Depends(get_schema_manager),
    _: None = Depends(verify_api_key),
):
    await schema_manager.refresh()
    return {"status": "refreshed", "schema": schema_manager.get_schema()}


@router.get("/prompts/versions")
async def get_prompt_versions(_: None = Depends(verify_api_key)):
    from app.pipeline.prompt_registry import all_versions
    return all_versions()


@router.get("/models")
async def list_models(
    llm: LLMProvider = Depends(get_llm_provider),
    _: None = Depends(verify_api_key),
):
    if hasattr(llm, "list_models"):
        return {"models": await llm.list_models()}
    return {"models": []}


@router.get("/query/stream")
@limiter.limit(_ip_rate_limit)
async def query_stream(
    request: Request,
    query: str = Query(..., description="Natural language security query"),
    session_id: str | None = Query(None),
    orchestrator: PipelineOrchestrator = Depends(get_orchestrator),
    _: None = Depends(verify_api_key),
):
    """SSE endpoint — emits per-step progress then full result."""
    await _enforce_session_rate_limit(session_id)

    step_queue: asyncio.Queue = asyncio.Queue(maxsize=64)

    async def _run():
        try:
            result = await orchestrator.run_streaming(
                query=query, session_id=session_id, step_queue=step_queue
            )
            await step_queue.put({"step": "done", "result": "ok", "spl": result.spl})
        except IrrelevantQueryError:
            # Orchestrator already emitted a "not_applicable" step; just end cleanly.
            await step_queue.put({"step": "done", "result": "not_applicable"})
        except GuardrailViolation as e:
            await step_queue.put({"step": "error", "detail": f"Guardrail: {e.reason}"})
        except Exception as e:
            await step_queue.put({"step": "error", "detail": "Internal error"})
            _logger.exception("SSE stream error: %s", e)
        finally:
            await step_queue.put(None)  # sentinel

    pipeline_task = asyncio.ensure_future(_run())

    async def _event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    pipeline_task.cancel()
                    break
                try:
                    event = await asyncio.wait_for(step_queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if event is None:
                    break
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            pipeline_task.cancel()

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/health", response_model=HealthResponse)
@router.get("/health/live", response_model=HealthResponse)
async def health():
    """Liveness: process is up. Never touches dependencies."""
    return HealthResponse(status="ok")


@router.get("/health/ready")
async def health_ready(request: Request):
    """Readiness: pipeline wired + prompts loaded. 503 until startup completes.

    k8s should gate traffic on this; use /health/live for restart decisions.
    """
    ready = getattr(request.app.state, "ready", False)
    if not ready:
        raise HTTPException(status_code=503, detail="Service not ready")
    return {"status": "ready"}
