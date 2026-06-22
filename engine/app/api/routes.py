import asyncio
import json
import logging
import os
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Security
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.api.rate_limiter import limiter, check_session_rate_limit
from app.api.schemas import (
    QueryRequest, QueryResponse, QueryMetadata, ActionSuggestionSchema,
    InvestigationStepSchema, PlanSchema, AnomalySchema, PatternSchema,
    AnalysisSchema, DecisionSchema, ErrorResponse, HealthResponse,
)
from app.llm.base import LLMProvider
from app.pipeline.guardrails import GuardrailViolation
from app.pipeline.orchestrator import PipelineOrchestrator
from app.pipeline.schema_manager import SchemaManager

_SESSION_RATE_LIMIT = 10


router = APIRouter(prefix="/api")

_bearer = HTTPBearer(auto_error=False)


def _get_api_key() -> str:
    # Reads from env at call time so tests can override
    return os.environ.get("API_KEY", "")


def verify_api_key(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> None:
    api_key = _get_api_key()
    if not api_key:
        return  # auth disabled in dev mode
    if credentials is None or credentials.credentials != api_key:
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


@router.post("/query", response_model=QueryResponse)
@limiter.limit("60/minute")
async def query(
    body: QueryRequest,
    request: Request,
    orchestrator: PipelineOrchestrator = Depends(get_orchestrator),
    _: None = Depends(verify_api_key),
):
    try:
        if body.session_id:
            allowed = await check_session_rate_limit(body.session_id, limit=_SESSION_RATE_LIMIT)
            if not allowed:
                raise HTTPException(
                    status_code=429,
                    detail="Session rate limit exceeded",
                    headers={"Retry-After": "60"},
                )

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
                _logger = logging.getLogger("setuq.api")
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
        )
    except GuardrailViolation as e:
        raise HTTPException(status_code=422, detail=f"Guardrail violation: {e.reason}")
    except ConnectionError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        _logger = logging.getLogger("setuq.api")
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
@limiter.limit("30/minute")
async def query_stream(
    request: Request,
    query: str = Query(..., description="Natural language security query"),
    session_id: str | None = Query(None),
    orchestrator: PipelineOrchestrator = Depends(get_orchestrator),
    _: None = Depends(verify_api_key),
):
    """SSE endpoint — emits per-step progress then full result."""
    if session_id:
        allowed = await check_session_rate_limit(session_id, limit=_SESSION_RATE_LIMIT)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="Session rate limit exceeded",
                headers={"Retry-After": "60"},
            )

    step_queue: asyncio.Queue = asyncio.Queue(maxsize=64)

    async def _run():
        try:
            result = await orchestrator.run_streaming(
                query=query, session_id=session_id, step_queue=step_queue
            )
            await step_queue.put({"step": "done", "result": "ok", "spl": result.spl})
        except GuardrailViolation as e:
            await step_queue.put({"step": "error", "detail": f"Guardrail: {e.reason}"})
        except Exception as e:
            await step_queue.put({"step": "error", "detail": "Internal error"})
            _logger = logging.getLogger("setuq.api")
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
async def health():
    return HealthResponse(status="ok")
