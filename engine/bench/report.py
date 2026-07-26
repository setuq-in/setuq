from __future__ import annotations
import json
import time
from pathlib import Path


def persist_run(payload: dict, runs_dir: Path) -> Path:
    runs_dir.mkdir(parents=True, exist_ok=True)
    path = runs_dir / f"run-{time.time_ns()}.json"
    path.write_text(json.dumps(payload, indent=2))
    return path


def load_previous(runs_dir: Path) -> dict | None:
    if not runs_dir.exists():
        return None
    runs = sorted(runs_dir.glob("run-*.json"))
    if len(runs) < 2:
        return None
    return json.loads(runs[-2].read_text())


def emit_trend(current: dict, previous: dict | None) -> str:
    lines = ["| Stage | p95 ms | delta p95 |", "|-------|--------|-------|"]
    prev_stages = (previous or {}).get("stages", {})
    for stage, s in current.get("stages", {}).items():
        cur = s["p95"]
        prev = prev_stages.get(stage, {}).get("p95")
        delta = f"{cur - prev:+.2f}" if prev is not None else "—"
        lines.append(f"| {stage} | {cur:.2f} | {delta} |")
    cost = current.get("cost_usd_mean", 0.0)
    prev_cost = (previous or {}).get("cost_usd_mean")
    cost_delta = f"{cost - prev_cost:+.4f}" if prev_cost is not None else "—"
    header = f"# Bench trend\n\n**$/query mean:** {cost:.4f} (delta {cost_delta})\n"
    return header + "\n" + "\n".join(lines)
