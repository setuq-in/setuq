from __future__ import annotations

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
}

_PER_MILLION = 1_000_000


def compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return estimated USD cost. Returns 0.0 for unknown/local models."""
    prices = _MODEL_PRICES.get(model)
    if prices is None:
        return 0.0
    in_price, out_price = prices
    return (input_tokens * in_price + output_tokens * out_price) / _PER_MILLION
