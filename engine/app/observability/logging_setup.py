"""Central logging config for the engine.

Emits ECS (Elastic Common Schema) structured JSON to stdout when the
`ecs-logging` package is installed, otherwise falls back to plain text so the
app never fails to start over a missing optional dependency. OTel trace/span
ids are injected into every record so logs correlate with traces in Kibana /
the observability stack.

Call configure_logging(settings) once, first thing at startup.
"""
from __future__ import annotations
import logging
import sys

_configured = False


class _TraceContextFilter(logging.Filter):
    """Attach current OTel trace_id / span_id to each record (empty if none)."""

    def filter(self, record: logging.LogRecord) -> bool:
        trace_id = span_id = ""
        try:
            from opentelemetry import trace
            ctx = trace.get_current_span().get_span_context()
            if ctx.is_valid:
                trace_id = format(ctx.trace_id, "032x")
                span_id = format(ctx.span_id, "016x")
        except Exception:
            pass
        record.trace_id = trace_id
        record.span_id = span_id
        return True


def _make_formatter(ecs_enabled: bool) -> logging.Formatter:
    if ecs_enabled:
        try:
            import ecs_logging
            # Surface the injected ids as ECS trace.* fields.
            return ecs_logging.StdlibFormatter(
                extra={},
                exclude_fields=[],
            )
        except ImportError:
            logging.getLogger("setuq.logging").warning(
                "ecs-logging not installed — falling back to plain text logs. "
                "`pip install ecs-logging` for structured ECS output."
            )
    return logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] "
        "trace_id=%(trace_id)s %(message)s"
    )


def configure_logging(settings) -> None:
    """Install a single stdout handler on the root logger. Idempotent."""
    global _configured
    if _configured:
        return

    level = getattr(logging, str(settings.LOG_LEVEL).upper(), logging.INFO)
    ecs_enabled = getattr(settings, "LOG_ECS_ENABLED", True)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_make_formatter(ecs_enabled))
    handler.addFilter(_TraceContextFilter())

    root = logging.getLogger()
    # Drop any pre-existing handlers (e.g. uvicorn/basicConfig) so we don't
    # double-log; the audit logger keeps its own isolated handler (propagate=False).
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(level)

    # Keep uvicorn's access/error logs flowing through our formatter.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True

    logging.getLogger("setuq").setLevel(level)
    _configured = True
    logging.getLogger("setuq.logging").info(
        "logging configured level=%s ecs=%s", settings.LOG_LEVEL, ecs_enabled
    )
