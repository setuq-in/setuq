from __future__ import annotations
from dataclasses import dataclass


@dataclass
class GateResult:
    name: str
    passed: bool
    detail: str


def check_slo(payload: dict, thresholds: dict) -> list[GateResult]:
    out: list[GateResult] = []
    measured = {
        "e2e_p95_ms": payload.get("e2e", {}).get("p95", 0.0),
        "guardrail_p95_ms": payload.get("stages", {}).get("pipeline.guardrail", {}).get("p95", 0.0),
        "cost_usd_mean": payload.get("cost_usd_mean", 0.0),
    }
    for key, limit in thresholds.items():
        val = measured.get(key, 0.0)
        passed = val <= limit
        out.append(GateResult(key, passed, f"{val:.2f} <= {limit} : {passed}"))
    return out


def check_regression(current: dict, previous: dict | None, pct: float) -> list[GateResult]:
    if previous is None:
        return [GateResult("regression", True, "no previous run")]
    out: list[GateResult] = []
    prev_stages = previous.get("stages", {})
    for stage, s in current.get("stages", {}).items():
        prev_p95 = prev_stages.get(stage, {}).get("p95")
        if not prev_p95:
            continue
        cur_p95 = s.get("p95")
        if cur_p95 is None:
            continue
        growth = (cur_p95 - prev_p95) / prev_p95
        if growth > pct:
            out.append(GateResult(f"regression:{stage}", False,
                                  f"p95 grew {growth*100:.0f}% (> {pct*100:.0f}%)"))
    return out or [GateResult("regression", True, "no regressions")]


def all_passed(results: list[GateResult]) -> bool:
    return all(r.passed for r in results)
