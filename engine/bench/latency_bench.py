from __future__ import annotations
import argparse
import asyncio
import random
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

from app.pipeline import prompt_registry
from bench.capture import StageCapture
from bench.factory import build_bench_orchestrator
from bench.metrics import summarize
from bench.mocks import LatencyMockLLM
from bench.report import persist_run, load_previous, emit_trend
from eval.runner import _load_golden_queries

_RUNS_DIR = Path(__file__).parent / "runs"
_PROMPTS_PATH = Path(__file__).parent.parent / "config" / "prompts.yaml"


async def run_latency_bench(reps: int, warmup: int, delay_ms: int, tmp_path) -> dict:
    random.seed(1337)
    queries = _load_golden_queries()
    orch, audit = build_bench_orchestrator(tmp_path, llm=LatencyMockLLM(delay_ms=delay_ms))

    stage_samples: dict[str, list[float]] = {}
    e2e_samples: list[float] = []
    cost_samples: list[float] = []
    runs_ok = 0
    runs_errored = 0

    with StageCapture() as cap:
        for gq in queries:
            for i in range(reps):
                cap.clear()
                t0 = time.monotonic()
                try:
                    await orch.run(gq["query"], session_id=f"bench-{gq['id']}-{i}")
                    runs_ok += 1
                except Exception:
                    # guardrail/injection queries raise by design; still count their stage timings
                    runs_errored += 1
                e2e_ms = (time.monotonic() - t0) * 1000.0
                if i < warmup:
                    continue
                for stage, dur in cap.durations_ms().items():
                    stage_samples.setdefault(stage, []).append(dur)
                e2e_samples.append(e2e_ms)
                cost_samples.append(audit.last_cost())

    return {
        "timestamp": time.time(),
        "stages": {s: asdict(summarize(v)) for s, v in stage_samples.items()},
        "e2e": asdict(summarize(e2e_samples)),
        "cost_usd_mean": (sum(cost_samples) / len(cost_samples)) if cost_samples else 0.0,
        "runs_ok": runs_ok,
        "runs_errored": runs_errored,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 1 latency + cost bench")
    parser.add_argument("--reps", type=int, default=10)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--delay-ms", type=int, default=200)
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()

    # Standalone entrypoint: not run under pytest, so the shipped prompts
    # aren't loaded by tests/conftest.py's autouse fixture — load them here.
    if not prompt_registry.all_versions():
        prompt_registry.load(str(_PROMPTS_PATH))

    tmp = Path(tempfile.mkdtemp())
    payload = asyncio.run(run_latency_bench(args.reps, args.warmup, args.delay_ms, tmp))

    print(f"E2E p95={payload['e2e']['p95']:.1f}ms  $/query={payload['cost_usd_mean']:.4f}")
    for stage, s in sorted(payload["stages"].items()):
        print(f"  {stage:<32} p50={s['p50']:.1f} p95={s['p95']:.1f} p99={s['p99']:.1f}ms")

    if args.report:
        path = persist_run(payload, _RUNS_DIR)
        print(f"\nPersisted {path}")
        print(emit_trend(payload, load_previous(_RUNS_DIR)))


if __name__ == "__main__":
    main()
