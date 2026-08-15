import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from app.config import Settings
from app.api.rate_limiter import limiter, set_limiter_enabled
from app.api.routes import router, get_orchestrator, get_schema_manager, get_llm_provider

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler as _Scheduler
    _HAS_APSCHEDULER = True
except ImportError:
    _HAS_APSCHEDULER = False
from app.llm.factory import create_llm_provider
from app.llm.base import LLMProvider
from app.pipeline.action_suggester import ActionSuggester
from app.pipeline.analysis_agent import AnalysisAgent
from app.pipeline.chart_inferer import ChartInferer
from app.pipeline.audit_logger import init_audit_logger, get_audit_logger
from app.pipeline.decision_engine import DecisionEngine
from app.pipeline.guardrails import QueryGuardrail, load_guardrail_config
from app.pipeline.planner import PlannerAgent
from app.pipeline.relevance import RelevanceGate
from app.pipeline import prompt_registry
from app.pipeline.schema_manager import SchemaManager
from app.pipeline.redis_session_manager import create_session_manager
from app.pipeline.spl_generator import SPLGenerator
from app.pipeline.splunk_client import SplunkClient
from app.pipeline.summarizer import Summarizer
from app.pipeline.orchestrator import PipelineOrchestrator
from app.observability.tracer import init_tracer, shutdown_tracer
from app.observability.langfuse_client import init_langfuse, flush_langfuse
from app.observability.logging_setup import configure_logging

settings = Settings()

_log = logging.getLogger("setuq.main")


def _validate_startup_config(cfg: Settings) -> None:
    """Fail-closed in production; warn loudly in development.

    Weak defaults (no auth, wildcard CORS, TLS verify off) are convenient for
    local dev but dangerous in prod. In production we refuse to start.
    """
    origins = [o.strip() for o in cfg.UI_ORIGINS.split(",") if o.strip()]
    problems: list[str] = []
    if not cfg.API_KEY:
        problems.append("API_KEY is empty — the API is UNAUTHENTICATED")
    if "*" in origins or not origins:
        problems.append("UI_ORIGINS is a wildcard ('*') — any site can call the API")
    if not cfg.SPLUNK_VERIFY_SSL:
        problems.append("SPLUNK_VERIFY_SSL is false — Splunk TLS is not verified (MITM risk)")

    is_prod = cfg.ENVIRONMENT.strip().lower() == "production"
    if problems and is_prod:
        raise RuntimeError(
            "Refusing to start in production with insecure config:\n  - "
            + "\n  - ".join(problems)
        )
    for p in problems:
        _log.warning("INSECURE CONFIG (%s): %s", cfg.ENVIRONMENT, p)


_orchestrator: PipelineOrchestrator | None = None
_schema_manager: SchemaManager | None = None
_splunk_client: SplunkClient | None = None
_llm: LLMProvider | None = None
_scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _orchestrator, _schema_manager, _splunk_client, _llm, _scheduler

    # Structured ECS logging first so every startup line — including the
    # config-validation warnings below — is captured.
    configure_logging(settings)
    _log.info(
        "engine starting provider=%s model=%s obs_enabled=%s redis=%s",
        settings.LLM_PROVIDER, settings.LLM_MODEL,
        settings.OBSERVABILITY_ENABLED, bool(settings.REDIS_URL),
    )

    # Fail-closed in production on weak config (empty API_KEY, wildcard CORS,
    # unverified Splunk TLS); warn loudly in development.
    _validate_startup_config(settings)

    # Init observability (no-op if OBSERVABILITY_ENABLED=False)
    init_tracer(settings)
    init_langfuse(settings)
    from app.observability.langfuse_tracing import configure as configure_langfuse_tracing
    configure_langfuse_tracing(settings.LANGFUSE_LOG_PROMPTS)

    llm = create_llm_provider(settings)
    _llm = llm
    discovery = None
    if settings.SCHEMA_DISCOVERY_ENABLED:
        from app.pipeline.schema_discovery import SplunkSchemaDiscovery
        _splunk_client = SplunkClient(settings)
        discovery = SplunkSchemaDiscovery(_splunk_client)

    # Ollama runs local models with a small context window, so cap the schema
    # prompt whenever ollama may serve a request (primary or fallback);
    # large-context providers (anthropic/openai) get the full schema.
    fallback_providers = (
        [p.strip() for p in settings.FALLBACK_PROVIDERS.split(",")]
        if settings.FALLBACK_ENABLED else []
    )
    ollama_in_use = settings.LLM_PROVIDER == "ollama" or "ollama" in fallback_providers
    schema_max_chars = 24_000 if ollama_in_use else None
    _schema_manager = SchemaManager(
        overrides_path="schema_overrides.yaml",
        cache_path=settings.SCHEMA_CACHE_PATH if settings.SCHEMA_DISCOVERY_ENABLED else None,
        discovery=discovery,
        ttl_hours=settings.SCHEMA_REFRESH_HOURS,
        max_context_chars=schema_max_chars,
    )
    if _splunk_client is None:
        _splunk_client = SplunkClient(settings)
    session_manager = create_session_manager(settings, max_turns=settings.SESSION_MAX_TURNS)
    session_manager.start_cleanup_task()
    # Load all system prompts (YAML is the sole source) before agents serve
    # requests; agents resolve their prompt via prompt_registry.get() at call
    # time. Fail-closed: a missing/incomplete file aborts startup.
    n_prompts = prompt_registry.load(settings.PROMPTS_CONFIG_PATH)
    prompt_registry.ensure_complete()
    _log.info("Loaded %d prompt(s) from %s", n_prompts, settings.PROMPTS_CONFIG_PATH)
    spl_generator = SPLGenerator(llm=llm)
    summarizer = Summarizer(llm=llm)
    action_suggester = ActionSuggester(llm=llm)
    planner = PlannerAgent(llm=llm)
    analysis_agent = AnalysisAgent(llm=llm)
    decision_engine = DecisionEngine(llm=llm)
    relevance_gate = RelevanceGate(
        llm=llm,
        schema_manager=_schema_manager,
        enabled=settings.RELEVANCE_GATE_ENABLED,
    )
    chart_inferer = ChartInferer(llm=llm)
    audit_logger = init_audit_logger(settings.AUDIT_LOG_PATH)
    audit_logger.attach_loop(asyncio.get_running_loop())

    # Build guardrail with known indexes from schema + rules from YAML (sole
    # source of truth). Fail-closed: a missing/invalid file aborts startup.
    known_indexes = list(_schema_manager.get_schema().get("indexes", {}).keys())
    gconfig = load_guardrail_config(settings.GUARDRAILS_CONFIG_PATH)
    guardrail = QueryGuardrail(
        known_indexes=known_indexes,
        max_time_range_days=gconfig["max_time_range_days"],
        resource_heavy_patterns=gconfig["resource_heavy_patterns"],
    )
    # Keep guardrail in sync whenever schema refreshes (reactive or scheduled)
    _schema_manager._on_change = guardrail.update_known_indexes

    _orchestrator = PipelineOrchestrator(
        schema_manager=_schema_manager,
        spl_generator=spl_generator,
        splunk_client=_splunk_client,
        summarizer=summarizer,
        session_manager=session_manager,
        action_suggester=action_suggester,
        guardrail=guardrail,
        audit_logger=audit_logger,
        planner=planner,
        analysis_agent=analysis_agent,
        decision_engine=decision_engine,
        relevance_gate=relevance_gate,
        chart_inferer=chart_inferer,
    )

    app.dependency_overrides[get_orchestrator] = lambda: _orchestrator
    app.dependency_overrides[get_schema_manager] = lambda: _schema_manager
    app.dependency_overrides[get_llm_provider] = lambda: llm

    if _HAS_APSCHEDULER and settings.SCHEMA_DISCOVERY_ENABLED:
        _scheduler = _Scheduler()
        _scheduler.add_job(
            _schema_manager.refresh,
            "interval",
            hours=settings.SCHEMA_REFRESH_HOURS,
            kwargs={"max_indexes": settings.SCHEMA_MAX_INDEXES},
            id="schema_refresh",
        )
        _scheduler.start()

    app.state.ready = True
    yield
    app.state.ready = False

    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
    await session_manager.stop_cleanup_task()
    await get_audit_logger().aclose()
    flush_langfuse()
    shutdown_tracer()

    if _splunk_client:
        await _splunk_client.close()
    if _llm and hasattr(_llm, "close"):
        await _llm.close()


app = FastAPI(title="Setuq — Built to bridge. Splunk today, everything tomorrow.", lifespan=lifespan)
app.state.ready = False

set_limiter_enabled(settings.RATE_LIMIT_ENABLED)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

_origins = [o.strip() for o in settings.UI_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-Allow-Auto-Execute"],
    expose_headers=["Content-Type"],
)

app.include_router(router)
