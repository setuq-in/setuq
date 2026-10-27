from __future__ import annotations
from dataclasses import dataclass


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (p / 100.0) * (len(ordered) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    frac = rank - lo
    return float(ordered[lo] + (ordered[hi] - ordered[lo]) * frac)


@dataclass
class LatencySummary:
    count: int
    p50: float
    p95: float
    p99: float
    mean: float
    max: float


def summarize(values: list[float]) -> LatencySummary:
    if not values:
        return LatencySummary(0, 0.0, 0.0, 0.0, 0.0, 0.0)
    return LatencySummary(
        count=len(values),
        p50=percentile(values, 50),
        p95=percentile(values, 95),
        p99=percentile(values, 99),
        mean=sum(values) / len(values),
        max=float(max(values)),
    )
