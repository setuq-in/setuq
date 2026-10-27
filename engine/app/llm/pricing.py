from __future__ import annotations

import logging
import re

from opentelemetry import metrics as otel_metrics

_logger = logging.getLogger("setuq.pricing")
_meter = otel_metrics.get_meter("setuq.pricing")
_unpriced_counter = _meter.create_counter(
    "llm.unpriced_model_requests",
    description="Count of generate() calls billed at $0 because the model id has no pricing entry",
)

# Cost per 1M tokens: (input_usd, output_usd)
_MODEL_PRICES: dict[str, tuple[float, float]] = {
    # OpenAI
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4-turbo": (10.00, 30.00),
    "gpt-3.5-turbo": (0.50, 1.50),
    # Anthropic
    "claude-opus-4-7": (15.00, 75.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5-20251001": (0.80, 4.00),
    "claude-3-5-sonnet-20241022": (3.00, 15.00),
    "claude-3-5-haiku-20241022": (0.80, 4.00),
    "claude-3-opus-20240229": (15.00, 75.00),
    # Google Gemini (per 1M tokens; approximate, standard context tier)
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-1.5-pro": (1.25, 5.00),
    "gemini-1.5-flash": (0.075, 0.30),
}

_PER_MILLION = 1_000_000

# Dated snapshot suffixes (e.g. "-20250514" or "-2024-11-20") on otherwise-priced
# model ids — stripped before falling back to a prefix match.
_DATE_SUFFIX_RE = re.compile(r"-\d{4}-?\d{2}-?\d{2}$")


def _lookup_price(model: str) -> tuple[float, float] | None:
    if (prices := _MODEL_PRICES.get(model)) is not None:
        return prices
    normalized = _DATE_SUFFIX_RE.sub("", model)
    if (prices := _MODEL_PRICES.get(normalized)) is not None:
        return prices
    # Unambiguous prefix match only — e.g. "claude-sonnet-4" (a dated snapshot
    # with its date stripped) against table key "claude-sonnet-4-6".
    candidates = [
        k for k in _MODEL_PRICES if k.startswith(normalized) or normalized.startswith(k)
    ]
    if len(candidates) == 1:
        return _MODEL_PRICES[candidates[0]]
    return None


def compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return estimated USD cost. Returns 0.0 for unknown/local models — and
    emits a warning + counter so a silently-zero cost budget is observable."""
    prices = _lookup_price(model)
    if prices is None:
        _unpriced_counter.add(1, {"model": model})
        _logger.warning(
            "No pricing entry for model %r — cost is billed at $0.0 and the "
            "per-run cost budget will not bind for this call",
            model,
        )
        return 0.0
    in_price, out_price = prices
    return (input_tokens * in_price + output_tokens * out_price) / _PER_MILLION
