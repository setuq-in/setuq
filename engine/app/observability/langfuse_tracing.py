"""Native Langfuse (v2) trace/generation helpers.

Approach A: build a real Langfuse trace per pipeline run and nest one
generation per LLM call under it, so the Langfuse UI shows the full run with
per-call model, tokens and cost.

All functions are best-effort and never raise into the pipeline. When Langfuse
is not initialized (OBSERVABILITY_ENABLED off or missing keys) they no-op.

Prompt/output content is only sent when LANGFUSE_LOG_PROMPTS is on; otherwise
only hashes and numeric metadata leave the process.
"""
from __future__ import annotations
import logging

from app.observability.langfuse_client import get_langfuse
from app.observability.redact import hash_query

_logger = logging.getLogger("setuq.langfuse")

_LOG_PROMPTS = False


def configure(log_prompts: bool) -> None:
    """Set once at startup from settings.LANGFUSE_LOG_PROMPTS."""
    global _LOG_PROMPTS
    _LOG_PROMPTS = bool(log_prompts)


def log_prompts_enabled() -> bool:
    return _LOG_PROMPTS


def start_trace(trace_id: str, name: str, session_id: str, query: str):
    """Create a Langfuse trace bound to the OTel trace_id. Returns the trace
    client (for update_trace) or None."""
    lf = get_langfuse()
    if lf is None:
        return None
    try:
        return lf.trace(
            id=trace_id or None,
            name=name,
            session_id=session_id or None,
            input=query if _LOG_PROMPTS else {"query_hash": hash_query(query)},
        )
    except Exception:
        _logger.debug("langfuse start_trace failed", exc_info=True)
        return None


def update_trace(trace, output: dict) -> None:
    """Attach the run's output to the trace. Caller decides what is safe to put
    in `output` (see LANGFUSE_LOG_PROMPTS)."""
    if trace is None:
        return
    try:
        trace.update(output=output)
    except Exception:
        _logger.debug("langfuse update_trace failed", exc_info=True)


def log_generation(
    trace_id: str,
    provider: str,
    model: str,
    usage,
    user_prompt: str | None = None,
    output: str | None = None,
) -> None:
    """Record one LLM call as a Langfuse generation nested under trace_id."""
    lf = get_langfuse()
    if lf is None:
        return
    try:
        gen_usage = None
        if usage is not None:
            gen_usage = {
                "input": usage.input_tokens,
                "output": usage.output_tokens,
                "total": usage.input_tokens + usage.output_tokens,
                "unit": "TOKENS",
                "total_cost": usage.cost_usd,
            }
        lf.generation(
            trace_id=trace_id or None,
            name=f"{provider}.generate",
            model=model,
            input=user_prompt if _LOG_PROMPTS else None,
            output=output if _LOG_PROMPTS else None,
            usage=gen_usage,
            metadata={"provider": provider},
        )
    except Exception:
        _logger.debug("langfuse log_generation failed", exc_info=True)
