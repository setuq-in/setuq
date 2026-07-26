# Setuq Benchmarking Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a `engine/bench/` harness that benchmarks the agentic pipeline across latency, cost, throughput, resilience, and provider/scale, with CI regression gates — reusing the existing eval harness, OTel spans, budget accounting, and MockLLM.

**Architecture:** A `bench/` package sits beside `eval/`. It captures per-stage latency from the OTel spans the orchestrator already emits (via an in-process `InMemorySpanExporter`), reads per-run cost from the audit entry (`total_tokens`/`total_cost_usd`), and drives the pipeline through the blessed `tests.pipeline.test_orchestrator._build_orchestrator` factory. Perf/load/resilience runs use deterministic mock providers (Splunk is already `AsyncMock` in that factory); provider-matrix and cost runs use real providers. Results persist as JSON runs and are gated in CI by SLO thresholds + regression deltas.

**Tech Stack:** Python 3.13, pytest + pytest-asyncio, OpenTelemetry SDK (`opentelemetry-sdk` — already a dep), locust (new dev dep, Phase 2), stdlib `statistics`.

## Global Constraints

- Python **3.13**; `asyncio` throughout; tests use `@pytest.mark.asyncio`.
- **Reuse, don't rebuild.** Import `_build_orchestrator`, `MockLLM` from `tests/pipeline/test_orchestrator.py` (CLAUDE.md blesses this). Reuse `eval/runner.py::_load_golden_queries`, `_score_keywords`, `_score_spl_quality`. Reuse `app.llm.harness.get_run_budget_usage`. Reuse OTel span names emitted in `app/pipeline/orchestrator.py`.
- **Splunk is never hit in perf runs.** The factory injects `AsyncMock(spec=SplunkClient)`; keep it mocked for latency/load/resilience. Real Splunk is out of scope.
- **Exact stage span names** (from `orchestrator._run_inner`): `pipeline.run` (root), `pipeline.relevance`, `pipeline.get_schema`, `pipeline.plan_and_generate`, `pipeline.guardrail`, `pipeline.execute_and_explain`, `pipeline.summarize_analyze`, `pipeline.suggest_actions`, `pipeline.decide`. These are the ONLY stage names — do not invent others.
- **Cost is per-run total, latency is per-stage.** Per-stage cost is NOT attributable because stages run concurrently inside `asyncio.TaskGroup` and the budget contextvar is process-global. Do not claim per-stage cost.
- `get_run_budget_usage()` returns `(int, float)` and is **reset in `run()`'s `finally`** — capture cost from the audit entry, never after `run()` returns.
- Determinism: seed `random.seed(1337)` before any `random.sample`.
- Run all: `cd engine && python3 -m pytest tests/bench/ -q`. Bench scripts run as `cd engine && python3 -m bench.<module>`.
- Plan/report location for outputs: `engine/bench/runs/` (JSON), `docs/benchmark-baseline.md` (baseline numbers).

---

## File Structure

```
engine/bench/
  __init__.py            # empty package marker
  metrics.py             # percentile(), summarize(), LatencySummary dataclass — pure math, no I/O
  capture.py             # StageCapture: in-process OTel span capture → {stage: duration_ms}
  mocks.py               # LatencyMockLLM(delay_ms), FaultyMockLLM(mode) — deterministic providers
  factory.py             # build_bench_orchestrator(...) + CapturingAuditLogger (cost/latency sink)
  latency_bench.py       # Phase 1: per-stage latency + per-run cost, N reps, percentiles
  report.py              # persist run JSON, load previous, emit markdown trend vs baseline
  gates.py               # Phase 5: SLO thresholds + regression check, process exit code
  resilience_bench.py    # Phase 3: fault-injection matrix
  provider_matrix.py     # Phase 4: provider × session-backend quality/latency/cost grid
  load/
    locustfile.py        # Phase 2: locust user driving /api/query + SSE
    README.md            # how to run the load test
  runs/                  # persisted run JSON (gitignored except a baseline)
tests/bench/
  __init__.py
  test_metrics.py
  test_capture.py
  test_mocks.py
  test_factory.py
  test_gates.py
  test_report.py
docs/benchmark-baseline.md   # written by first Phase-1 run (Task 10)
```

Each `bench/*.py` has one responsibility; `metrics.py` and `gates.py` are pure and fully unit-tested. The runner scripts (`latency_bench`, `resilience_bench`, `provider_matrix`) are thin orchestration over tested primitives.

---

## Task 1: Percentile metrics primitives

**Files:**
- Create: `engine/bench/__init__.py` (empty)
- Create: `engine/bench/metrics.py`
- Create: `engine/tests/bench/__init__.py` (empty)
- Test: `engine/tests/bench/test_metrics.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `percentile(values: list[float], p: float) -> float` — `p` in `[0,100]`, linear-interpolation, empty list → `0.0`.
  - `@dataclass LatencySummary` with fields `count: int, p50: float, p95: float, p99: float, mean: float, max: float`.
  - `summarize(values: list[float]) -> LatencySummary`.

- [ ] **Step 1: Write the failing test**

```python
# engine/tests/bench/test_metrics.py
from bench.metrics import percentile, summarize, LatencySummary


def test_percentile_basic():
    data = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert percentile(data, 50) == 5.5
    assert percentile(data, 100) == 10.0
    assert percentile(data, 0) == 1.0


def test_percentile_empty_is_zero():
    assert percentile([], 95) == 0.0


def test_summarize_fields():
    s = summarize([10.0, 20.0, 30.0, 40.0])
    assert isinstance(s, LatencySummary)
    assert s.count == 4
    assert s.max == 40.0
    assert s.mean == 25.0
    assert s.p50 == 25.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd engine && python3 -m pytest tests/bench/test_metrics.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bench'` (package not created yet).

- [ ] **Step 3: Write minimal implementation**

```python
# engine/bench/metrics.py
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
```

Also create empty files:
```python
# engine/bench/__init__.py  -> (empty)
# engine/tests/bench/__init__.py  -> (empty)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd engine && python3 -m pytest tests/bench/test_metrics.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add engine/bench/__init__.py engine/bench/metrics.py engine/tests/bench/__init__.py engine/tests/bench/test_metrics.py
git commit -m "feat(bench): percentile + latency summary primitives"
```

---

## Task 2: In-process stage-latency capture

**Files:**
- Create: `engine/bench/capture.py`
- Test: `engine/tests/bench/test_capture.py`

**Interfaces:**
- Consumes: nothing (installs its own OTel provider).
- Produces:
  - `class StageCapture` — context manager. `__enter__` installs a `TracerProvider` with `SimpleSpanProcessor(InMemorySpanExporter)` as the global provider (idempotent: installs once, reused). `clear()` empties captured spans. `durations_ms() -> dict[str, float]` maps span name → duration in ms for the most recent run (last occurrence wins).
  - Rationale: `orchestrator` calls `get_tracer()` → `trace.get_tracer_provider()`. When `OBSERVABILITY_ENABLED=false` the global provider is the no-op default, so we may install ours. Spans the orchestrator opens (`pipeline.*`) then land in our in-memory exporter.

- [ ] **Step 1: Write the failing test**

```python
# engine/tests/bench/test_capture.py
import pytest
from opentelemetry import trace
from bench.capture import StageCapture


def test_capture_records_span_durations():
    with StageCapture() as cap:
        tracer = trace.get_tracer("test")
        with tracer.start_as_current_span("pipeline.guardrail"):
            pass
        durations = cap.durations_ms()
    assert "pipeline.guardrail" in durations
    assert durations["pipeline.guardrail"] >= 0.0


def test_capture_clear_resets():
    with StageCapture() as cap:
        tracer = trace.get_tracer("test")
        with tracer.start_as_current_span("pipeline.decide"):
            pass
        cap.clear()
        assert cap.durations_ms() == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd engine && python3 -m pytest tests/bench/test_capture.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bench.capture'`.

- [ ] **Step 3: Write minimal implementation**

```python
# engine/bench/capture.py
from __future__ import annotations
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

_exporter: InMemorySpanExporter | None = None
_installed = False


class StageCapture:
    def __enter__(self) -> "StageCapture":
        global _exporter, _installed
        if not _installed:
            provider = TracerProvider()
            _exporter = InMemorySpanExporter()
            provider.add_span_processor(SimpleSpanProcessor(_exporter))
            trace.set_tracer_provider(provider)
            _installed = True
        assert _exporter is not None
        _exporter.clear()
        return self

    def __exit__(self, *exc) -> None:
        return None

    def clear(self) -> None:
        if _exporter is not None:
            _exporter.clear()

    def durations_ms(self) -> dict[str, float]:
        out: dict[str, float] = {}
        if _exporter is None:
            return out
        for span in _exporter.get_finished_spans():
            out[span.name] = (span.end_time - span.start_time) / 1_000_000.0
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd engine && python3 -m pytest tests/bench/test_capture.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add engine/bench/capture.py engine/tests/bench/test_capture.py
git commit -m "feat(bench): in-process OTel stage-latency capture"
```

---

## Task 3: Deterministic mock providers (latency + faults)

**Files:**
- Create: `engine/bench/mocks.py`
- Test: `engine/tests/bench/test_mocks.py`

**Interfaces:**
- Consumes: `MockLLM` from `tests/pipeline/test_orchestrator.py`, `LLMProvider` from `app.llm.base`.
- Produces:
  - `class LatencyMockLLM(MockLLM)` — `__init__(self, delay_ms: int = 200)`; `generate` sleeps `delay_ms/1000` then returns the same content as `MockLLM`. Used to simulate a real provider's wall-clock without token variance.
  - `class FaultyMockLLM(LLMProvider)` — `__init__(self, mode: str)`; `mode` in `{"timeout", "error"}`. `"timeout"` sleeps 10s (so `HarnessedProvider`'s `asyncio.wait_for` fires). `"error"` raises `RuntimeError("injected LLM failure")`.

- [ ] **Step 1: Write the failing test**

```python
# engine/tests/bench/test_mocks.py
import time
import pytest
from bench.mocks import LatencyMockLLM, FaultyMockLLM


@pytest.mark.asyncio
async def test_latency_mock_sleeps():
    llm = LatencyMockLLM(delay_ms=50)
    t0 = time.monotonic()
    resp = await llm.generate("SPL query generator", [], "q")
    assert (time.monotonic() - t0) >= 0.05
    assert "index=" in resp.content


@pytest.mark.asyncio
async def test_faulty_mock_error_raises():
    llm = FaultyMockLLM(mode="error")
    with pytest.raises(RuntimeError, match="injected LLM failure"):
        await llm.generate("any", [], "q")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd engine && python3 -m pytest tests/bench/test_mocks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bench.mocks'`.

- [ ] **Step 3: Write minimal implementation**

```python
# engine/bench/mocks.py
from __future__ import annotations
import asyncio
from app.llm.base import LLMProvider, LLMResponse, LLMUsage
from tests.pipeline.test_orchestrator import MockLLM


class LatencyMockLLM(MockLLM):
    def __init__(self, delay_ms: int = 200) -> None:
        self._delay = delay_ms / 1000.0

    async def generate(self, system_prompt: str, history: list[dict], user_prompt: str) -> LLMResponse:
        await asyncio.sleep(self._delay)
        return await super().generate(system_prompt, history, user_prompt)


class FaultyMockLLM(LLMProvider):
    def __init__(self, mode: str) -> None:
        assert mode in ("timeout", "error")
        self._mode = mode

    async def generate(self, system_prompt: str, history: list[dict], user_prompt: str) -> LLMResponse:
        if self._mode == "timeout":
            await asyncio.sleep(10.0)
            return LLMResponse(content="", usage=None)
        raise RuntimeError("injected LLM failure")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd engine && python3 -m pytest tests/bench/test_mocks.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add engine/bench/mocks.py engine/tests/bench/test_mocks.py
git commit -m "feat(bench): latency + fault-injecting mock LLM providers"
```

---

## Task 4: Bench orchestrator factory with cost/latency sink

**Files:**
- Create: `engine/bench/factory.py`
- Test: `engine/tests/bench/test_factory.py`

**Interfaces:**
- Consumes: `_build_orchestrator` from `tests/pipeline/test_orchestrator.py`, `AuditLogger`/`AuditEntry` from `app.pipeline.audit_logger`, `PipelineOrchestrator`.
- Produces:
  - `class CapturingAuditLogger(AuditLogger)` — overrides `log(entry)` to append `entry` to `self.entries: list` then call `super().log(entry)`. Exposes `last_cost() -> float` (`entries[-1].total_cost_usd or 0.0`) and `last_tokens() -> int`.
  - `build_bench_orchestrator(tmp_path, llm, splunk_return=None) -> tuple[PipelineOrchestrator, CapturingAuditLogger]` — builds via the blessed factory, swaps in a `CapturingAuditLogger`, disables idempotency (`orch.configure_idempotency(enabled=False)`) so repeated identical queries actually re-run.

- [ ] **Step 1: Write the failing test**

```python
# engine/tests/bench/test_factory.py
import pytest
from bench.factory import build_bench_orchestrator
from bench.mocks import LatencyMockLLM


@pytest.mark.asyncio
async def test_factory_runs_and_captures_cost(tmp_path):
    orch, audit = build_bench_orchestrator(tmp_path, llm=LatencyMockLLM(delay_ms=1))
    result = await orch.run("Show me total revenue by store", session_id="bench-1")
    assert result.spl
    assert audit.last_cost() == 0.0          # MockLLM cost is zero
    assert len(audit.entries) == 1


@pytest.mark.asyncio
async def test_factory_idempotency_disabled(tmp_path):
    orch, audit = build_bench_orchestrator(tmp_path, llm=LatencyMockLLM(delay_ms=1))
    await orch.run("same query", session_id="bench-2")
    await orch.run("same query", session_id="bench-2")
    assert len(audit.entries) == 2           # both runs executed, no cache short-circuit
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd engine && python3 -m pytest tests/bench/test_factory.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bench.factory'`.

- [ ] **Step 3: Write minimal implementation**

```python
# engine/bench/factory.py
from __future__ import annotations
from app.pipeline.audit_logger import AuditLogger, AuditEntry
from app.pipeline.orchestrator import PipelineOrchestrator
from tests.pipeline.test_orchestrator import _build_orchestrator


class CapturingAuditLogger(AuditLogger):
    def __init__(self, log_path: str) -> None:
        super().__init__(log_path=log_path)
        self.entries: list[AuditEntry] = []

    def log(self, entry: AuditEntry) -> None:
        self.entries.append(entry)
        super().log(entry)

    def last_cost(self) -> float:
        return (self.entries[-1].total_cost_usd if self.entries else 0.0) or 0.0

    def last_tokens(self) -> int:
        return (self.entries[-1].total_tokens if self.entries else 0) or 0


def build_bench_orchestrator(tmp_path, llm, splunk_return=None):
    from tests.pipeline.test_orchestrator import _SENTINEL
    orch = _build_orchestrator(
        tmp_path,
        llm=llm,
        splunk_return=_SENTINEL if splunk_return is None else splunk_return,
    )
    audit = CapturingAuditLogger(log_path=str(tmp_path / "bench_audit.log"))
    orch._audit_logger = audit
    orch.configure_idempotency(enabled=False)
    return orch, audit
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd engine && python3 -m pytest tests/bench/test_factory.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add engine/bench/factory.py engine/tests/bench/test_factory.py
git commit -m "feat(bench): bench orchestrator factory with capturing audit sink"
```

---

## Task 5: Report — persist runs, load previous, emit trend markdown

**Files:**
- Create: `engine/bench/report.py`
- Test: `engine/tests/bench/test_report.py`

**Interfaces:**
- Consumes: `LatencySummary` from `bench.metrics`.
- Produces:
  - `persist_run(payload: dict, runs_dir: Path) -> Path` — writes `run-<epoch>.json`, returns path.
  - `load_previous(runs_dir: Path) -> dict | None` — second-most-recent run JSON, or `None` if `< 2` runs.
  - `emit_trend(current: dict, previous: dict | None) -> str` — markdown table comparing per-stage p95 + `$/query` between runs, one row per stage, with a `Δ` column.
  - Run payload shape (produced by Task 6):
    `{"timestamp": float, "stages": {stage: {"p50":..,"p95":..,"p99":..,"mean":..,"max":..}}, "cost_usd_mean": float, "e2e": {..summary..}}`.

- [ ] **Step 1: Write the failing test**

```python
# engine/tests/bench/test_report.py
import json
from pathlib import Path
from bench.report import persist_run, load_previous, emit_trend


def _payload(p95: float, cost: float) -> dict:
    return {
        "timestamp": 1.0,
        "stages": {"pipeline.guardrail": {"p50": 1, "p95": p95, "p99": 3, "mean": 1, "max": 3}},
        "cost_usd_mean": cost,
        "e2e": {"p95": p95},
    }


def test_persist_and_load_previous(tmp_path: Path):
    persist_run(_payload(10, 0.01), tmp_path)
    persist_run(_payload(20, 0.02), tmp_path)
    prev = load_previous(tmp_path)
    assert prev["cost_usd_mean"] == 0.01     # older of the two


def test_load_previous_none_when_single(tmp_path: Path):
    persist_run(_payload(10, 0.01), tmp_path)
    assert load_previous(tmp_path) is None


def test_emit_trend_shows_delta():
    md = emit_trend(_payload(20, 0.02), _payload(10, 0.01))
    assert "pipeline.guardrail" in md
    assert "+10" in md or "+10.00" in md
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd engine && python3 -m pytest tests/bench/test_report.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bench.report'`.

- [ ] **Step 3: Write minimal implementation**

```python
# engine/bench/report.py
from __future__ import annotations
import json
import time
from pathlib import Path


def persist_run(payload: dict, runs_dir: Path) -> Path:
    runs_dir.mkdir(parents=True, exist_ok=True)
    path = runs_dir / f"run-{int(payload.get('timestamp') or time.time())}-{int(time.time()*1000)%100000}.json"
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
    lines = ["| Stage | p95 ms | Δ p95 |", "|-------|--------|-------|"]
    prev_stages = (previous or {}).get("stages", {})
    for stage, s in current.get("stages", {}).items():
        cur = s["p95"]
        prev = prev_stages.get(stage, {}).get("p95")
        delta = f"{cur - prev:+.2f}" if prev is not None else "—"
        lines.append(f"| {stage} | {cur:.2f} | {delta} |")
    cost = current.get("cost_usd_mean", 0.0)
    prev_cost = (previous or {}).get("cost_usd_mean")
    cost_delta = f"{cost - prev_cost:+.4f}" if prev_cost is not None else "—"
    header = f"# Bench trend\n\n**$/query mean:** {cost:.4f} (Δ {cost_delta})\n"
    return header + "\n" + "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd engine && python3 -m pytest tests/bench/test_report.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add engine/bench/report.py engine/tests/bench/test_report.py
git commit -m "feat(bench): run persistence + trend report"
```

---

## Task 6: Phase 1 — latency + cost bench runner

**Files:**
- Create: `engine/bench/latency_bench.py`
- Test: `engine/tests/bench/test_latency_bench.py`

**Interfaces:**
- Consumes: `StageCapture` (bench.capture), `summarize` (bench.metrics), `build_bench_orchestrator` (bench.factory), `LatencyMockLLM` (bench.mocks), `persist_run`/`emit_trend`/`load_previous` (bench.report), `_load_golden_queries` from `eval.runner`.
- Produces:
  - `async def run_latency_bench(reps: int, warmup: int, delay_ms: int, tmp_path) -> dict` — runs each golden query `reps` times through a `LatencyMockLLM(delay_ms)` orchestrator, discards first `warmup` reps, captures per-stage durations via `StageCapture`, per-run cost via the capturing audit logger. Returns a payload with `stages` (per-stage `LatencySummary` as dict), `e2e` summary, and `cost_usd_mean`.
  - `main()` — argparse (`--reps`, `--warmup`, `--delay-ms`, `--report`), runs the bench, prints summary, persists + prints trend when `--report`.
- Note: uses a single `pytest`-provided `tmp_path` when tested; `main()` uses `tempfile.mkdtemp()`.

- [ ] **Step 1: Write the failing test**

```python
# engine/tests/bench/test_latency_bench.py
import pytest
from bench.latency_bench import run_latency_bench


@pytest.mark.asyncio
async def test_latency_bench_produces_stage_summaries(tmp_path):
    payload = await run_latency_bench(reps=3, warmup=1, delay_ms=1, tmp_path=tmp_path)
    assert "stages" in payload
    # guardrail stage always runs for a valid query
    assert "pipeline.guardrail" in payload["stages"]
    assert payload["stages"]["pipeline.guardrail"]["count"] >= 1
    assert "e2e" in payload
    assert payload["cost_usd_mean"] == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd engine && python3 -m pytest tests/bench/test_latency_bench.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bench.latency_bench'`.

- [ ] **Step 3: Write minimal implementation**

```python
# engine/bench/latency_bench.py
from __future__ import annotations
import argparse
import asyncio
import random
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

from bench.capture import StageCapture
from bench.factory import build_bench_orchestrator
from bench.metrics import summarize
from bench.mocks import LatencyMockLLM
from bench.report import persist_run, load_previous, emit_trend
from eval.runner import _load_golden_queries

_RUNS_DIR = Path(__file__).parent / "runs"


async def run_latency_bench(reps: int, warmup: int, delay_ms: int, tmp_path) -> dict:
    random.seed(1337)
    queries = _load_golden_queries()
    orch, audit = build_bench_orchestrator(tmp_path, llm=LatencyMockLLM(delay_ms=delay_ms))

    stage_samples: dict[str, list[float]] = {}
    e2e_samples: list[float] = []
    cost_samples: list[float] = []

    with StageCapture() as cap:
        for gq in queries:
            for i in range(reps):
                cap.clear()
                t0 = time.monotonic()
                try:
                    await orch.run(gq["query"], session_id=f"bench-{gq['id']}-{i}")
                except Exception:
                    # guardrail/injection queries raise by design; still count their stage timings
                    pass
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
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 1 latency + cost bench")
    parser.add_argument("--reps", type=int, default=10)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--delay-ms", type=int, default=200)
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd engine && python3 -m pytest tests/bench/test_latency_bench.py -v`
Expected: PASS (1 test).

- [ ] **Step 5: Smoke-run the CLI**

Run: `cd engine && python3 -m bench.latency_bench --reps 3 --warmup 1 --delay-ms 5`
Expected: prints an `E2E p95=...` line and per-stage rows including `pipeline.guardrail`.

- [ ] **Step 6: Commit**

```bash
git add engine/bench/latency_bench.py engine/tests/bench/test_latency_bench.py
git commit -m "feat(bench): phase 1 per-stage latency + cost runner"
```

---

## Task 7: Phase 5 — SLO gates + regression check

**Files:**
- Create: `engine/bench/gates.py`
- Test: `engine/tests/bench/test_gates.py`

**Interfaces:**
- Consumes: run payload dict (Task 6 shape), previous payload (via `bench.report.load_previous`).
- Produces:
  - `@dataclass GateResult` with `name: str, passed: bool, detail: str`.
  - `check_slo(payload: dict, thresholds: dict) -> list[GateResult]` — hard SLO gates. `thresholds` keys: `e2e_p95_ms`, `guardrail_p95_ms`, `cost_usd_mean`. A gate fails when the measured value exceeds its threshold.
  - `check_regression(current: dict, previous: dict | None, pct: float) -> list[GateResult]` — flags any stage whose p95 rose > `pct` fraction vs previous (skip if no previous). One `GateResult` per regressed stage; a single passing result named `"regression"` when none.
  - `all_passed(results: list[GateResult]) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# engine/tests/bench/test_gates.py
from bench.gates import check_slo, check_regression, all_passed


def _payload(e2e_p95, guard_p95, cost):
    return {
        "stages": {"pipeline.guardrail": {"p95": guard_p95}},
        "e2e": {"p95": e2e_p95},
        "cost_usd_mean": cost,
    }


def test_slo_fails_when_over_threshold():
    res = check_slo(_payload(9000, 2, 0.01),
                    {"e2e_p95_ms": 6000, "guardrail_p95_ms": 5, "cost_usd_mean": 0.05})
    assert not all_passed(res)
    assert any(r.name == "e2e_p95_ms" and not r.passed for r in res)


def test_slo_passes_within_threshold():
    res = check_slo(_payload(3000, 2, 0.01),
                    {"e2e_p95_ms": 6000, "guardrail_p95_ms": 5, "cost_usd_mean": 0.05})
    assert all_passed(res)


def test_regression_flags_growth():
    cur = _payload(6000, 20, 0.01)
    prev = _payload(6000, 10, 0.01)
    res = check_regression(cur, prev, pct=0.5)     # 100% growth > 50%
    assert not all_passed(res)


def test_regression_none_without_previous():
    res = check_regression(_payload(1, 1, 0.01), None, pct=0.5)
    assert all_passed(res)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd engine && python3 -m pytest tests/bench/test_gates.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bench.gates'`.

- [ ] **Step 3: Write minimal implementation**

```python
# engine/bench/gates.py
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
        growth = (s["p95"] - prev_p95) / prev_p95
        if growth > pct:
            out.append(GateResult(f"regression:{stage}", False,
                                  f"p95 grew {growth*100:.0f}% (> {pct*100:.0f}%)"))
    return out or [GateResult("regression", True, "no regressions")]


def all_passed(results: list[GateResult]) -> bool:
    return all(r.passed for r in results)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd engine && python3 -m pytest tests/bench/test_gates.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add engine/bench/gates.py engine/tests/bench/test_gates.py
git commit -m "feat(bench): SLO + regression gates"
```

---

## Task 8: Phase 3 — resilience / fault-injection matrix

**Files:**
- Create: `engine/bench/resilience_bench.py`
- Test: `engine/tests/bench/test_resilience_bench.py`

**Interfaces:**
- Consumes: `build_bench_orchestrator` (bench.factory), `FaultyMockLLM` (bench.mocks), `MockLLM` (tests factory), `GuardrailViolation` from `app.pipeline.guardrails`.
- Produces:
  - `@dataclass FaultOutcome` with `fault: str, served: bool, audited: bool, error: str | None`.
  - `async def run_resilience_bench(tmp_path) -> list[FaultOutcome]` — drives a fixed set of fault scenarios and records, for each: did the request degrade gracefully (either a clean result OR a clean raise, never a hang), and was the outcome audited?
  - Fault scenarios (this release): `"llm_error"` (FaultyMockLLM error → expect raise, audited=False since no AuditEntry is written on hard LLM failure — record actual), `"guardrail_block"` (destructive query via normal MockLLM → expect `GuardrailViolation`), `"splunk_down"` (`splunk_return` set to a callable that raises → expect raise).
- Note: This bench asserts *behavior*, not thresholds. It documents what actually happens under each fault so regressions in resilience are visible.

- [ ] **Step 1: Write the failing test**

```python
# engine/tests/bench/test_resilience_bench.py
import pytest
from bench.resilience_bench import run_resilience_bench, FaultOutcome


@pytest.mark.asyncio
async def test_resilience_matrix_covers_faults(tmp_path):
    outcomes = await run_resilience_bench(tmp_path)
    faults = {o.fault for o in outcomes}
    assert {"llm_error", "guardrail_block", "splunk_down"} <= faults
    # guardrail block must be a clean, served-as-rejected outcome
    gb = next(o for o in outcomes if o.fault == "guardrail_block")
    assert gb.error is not None            # raised GuardrailViolation, did not hang
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd engine && python3 -m pytest tests/bench/test_resilience_bench.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bench.resilience_bench'`.

- [ ] **Step 3: Write minimal implementation**

```python
# engine/bench/resilience_bench.py
from __future__ import annotations
import asyncio
from dataclasses import dataclass

from app.pipeline.guardrails import GuardrailViolation
from bench.factory import build_bench_orchestrator
from bench.mocks import FaultyMockLLM
from tests.pipeline.test_orchestrator import MockLLM


@dataclass
class FaultOutcome:
    fault: str
    served: bool
    audited: bool
    error: str | None


async def _drive(orch, audit, query: str) -> tuple[bool, bool, str | None]:
    before = len(audit.entries)
    try:
        await asyncio.wait_for(orch.run(query, session_id="resil"), timeout=5.0)
        served = True
        error = None
    except asyncio.TimeoutError:
        return (False, False, "TIMEOUT (hang)")
    except Exception as exc:
        served = False
        error = f"{type(exc).__name__}: {exc}"
    audited = len(audit.entries) > before
    return (served, audited, error)


async def run_resilience_bench(tmp_path) -> list[FaultOutcome]:
    outcomes: list[FaultOutcome] = []

    # 1. Hard LLM failure — expect a clean raise (harness exhausts retries), not a hang.
    orch, audit = build_bench_orchestrator(tmp_path / "llm", llm=FaultyMockLLM(mode="error"))
    served, audited, err = await _drive(orch, audit, "Show failed logins")
    outcomes.append(FaultOutcome("llm_error", served, audited, err))

    # 2. Guardrail block — destructive query, normal LLM. Expect GuardrailViolation.
    orch, audit = build_bench_orchestrator(tmp_path / "gb", llm=MockLLM())
    served, audited, err = await _drive(orch, audit, "DELETE all logs | delete")
    outcomes.append(FaultOutcome("guardrail_block", served, audited, err))

    # 3. Splunk down — execute_spl raises. Expect a clean raise, no hang.
    def _boom(*a, **k):
        raise RuntimeError("splunk unreachable")
    orch, audit = build_bench_orchestrator(tmp_path / "sd", llm=MockLLM())
    orch._splunk_client.execute_spl.side_effect = _boom
    served, audited, err = await _drive(orch, audit, "Show me total revenue by store")
    outcomes.append(FaultOutcome("splunk_down", served, audited, err))

    return outcomes


if __name__ == "__main__":
    import tempfile
    from pathlib import Path
    res = asyncio.run(run_resilience_bench(Path(tempfile.mkdtemp())))
    for o in res:
        print(f"{o.fault:<16} served={o.served} audited={o.audited} error={o.error}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd engine && python3 -m pytest tests/bench/test_resilience_bench.py -v`
Expected: PASS (1 test). If `guardrail_block` does not raise, the MockLLM SPL for a delete query is not tripping the guardrail — adjust the query string to one the guardrail blocks (e.g. append `| delete`), since `MockLLM` returns a fixed SPL regardless; **instead** inject SPL directly by using a query the guardrail rejects on the generated SPL. If MockLLM's fixed SPL never trips guardrail, use `orch._guardrail` with a known-bad SPL by setting `splunk_return` unused and asserting on a raise from a resource-heavy pattern. Verify against `config/guardrails.yaml` patterns.

- [ ] **Step 5: Commit**

```bash
git add engine/bench/resilience_bench.py engine/tests/bench/test_resilience_bench.py
git commit -m "feat(bench): phase 3 resilience/fault-injection matrix"
```

---

## Task 9: Phase 2 — load harness (locust)

**Files:**
- Create: `engine/bench/load/locustfile.py`
- Create: `engine/bench/load/README.md`
- Modify: `engine/requirements-dev.txt` (add `locust`) — verify exact filename first with `ls engine/*.txt engine/pyproject.toml`.

**Interfaces:**
- Consumes: the running FastAPI app at `/api/query` and `/api/query/stream`.
- Produces: a locust `HttpUser` with two tasks: a weighted `POST /api/query` (70% easy, 20% multi-step, 10% guardrail bodies) and an SSE `GET /api/query/stream` task that asserts the stream ends with a `done` or `error` event.
- Note: The app under load MUST be started with a stub/mock LLM to measure pipeline+HTTP capacity, not provider latency. Document in README: set `LLM_PROVIDER` to a local/stub and `RATE_LIMIT_ENABLED` per the two runs (raw capacity vs limiter correctness). No unit test — this is an operational script; validate by running it.

- [ ] **Step 1: Confirm the dev-deps file**

Run: `ls engine/requirements*.txt engine/pyproject.toml 2>/dev/null`
Expected: identifies where dev deps live. Add `locust` there.

- [ ] **Step 2: Write the locustfile**

```python
# engine/bench/load/locustfile.py
import json
import random
from locust import HttpUser, task, between

_EASY = ["List all events from yesterday in the firewall index",
         "Show me top 5 user agents seen today",
         "Count alerts grouped by severity in the last 24 hours"]
_MULTI = ["Investigate why our auth service crashed yesterday — find the root cause across logs and metrics",
          "Compare login failure rates this week vs last week and identify which users had the biggest increase"]
_GUARD = ["DELETE all logs older than a year from the main index",
          "Show me everything across all indexes for the last 365 days"]


def _pick() -> str:
    r = random.random()
    if r < 0.70:
        return random.choice(_EASY)
    if r < 0.90:
        return random.choice(_MULTI)
    return random.choice(_GUARD)


class SetuqUser(HttpUser):
    wait_time = between(0.1, 0.5)

    @task(4)
    def query(self):
        self.client.post("/api/query", json={"query": _pick()}, name="POST /api/query")

    @task(1)
    def stream(self):
        with self.client.get("/api/query/stream", params={"query": random.choice(_EASY)},
                             stream=True, name="GET /api/query/stream", catch_response=True) as resp:
            saw_terminal = any((b"done" in line or b"error" in line) for line in resp.iter_lines())
            resp.success() if saw_terminal else resp.failure("no terminal SSE event")
```

- [ ] **Step 3: Write the README**

```markdown
# Load test (Phase 2)

Start the app with a stub LLM (no real provider latency):

    cd engine && LLM_PROVIDER=stub RATE_LIMIT_ENABLED=false uvicorn app.main:app --port 8000

Run 1 — raw capacity (limiter off), ramp 1→100:

    locust -f bench/load/locustfile.py --host http://localhost:8000 \
      --users 100 --spawn-rate 10 --run-time 3m --headless

Run 2 — limiter correctness (limiter on): expect 429s at >60/min-IP.
Record: RPS, p95, error%, knee. Verify SSE tasks all end with a terminal event.
```

- [ ] **Step 4: Verify locust loads the file**

Run: `cd engine && locust -f bench/load/locustfile.py --host http://localhost:8000 --headless -u 1 -r 1 --run-time 3s || true`
Expected: locust starts (may report connection errors if app not running — that's fine; we're verifying the file parses and registers `SetuqUser`).

- [ ] **Step 5: Commit**

```bash
git add engine/bench/load/locustfile.py engine/bench/load/README.md engine/requirements-dev.txt
git commit -m "feat(bench): phase 2 locust load harness"
```

---

## Task 10: Phase 4 — provider/scale matrix + CI wiring + baseline

**Files:**
- Create: `engine/bench/provider_matrix.py`
- Create: `docs/benchmark-baseline.md` (generated content, committed as first baseline)
- Create/Modify: CI workflow — verify existing CI first with `ls .github/workflows/`; add a `bench` job or extend the test job.
- Modify: `engine/bench/runs/.gitignore` (ignore `run-*.json` except a committed baseline) — create dir + gitignore.

**Interfaces:**
- Consumes: `Settings` from `app.config`, `create_llm_provider` from `app.llm.factory`, `run_latency_bench` (bench.latency_bench), `_load_golden_queries` + scoring from `eval.runner`.
- Produces:
  - `async def run_provider_cell(provider: str, session_backend: str) -> dict` — sets `Settings(LLM_PROVIDER=provider, REDIS_URL=... if backend=="redis")`, builds a real orchestrator, runs the golden quality suite, returns `{provider, backend, keyword_pass, judge_correctness?, e2e_p95_ms, cost_usd_mean}`.
  - `main()` — iterates the matrix (`anthropic`, `openai`, `ollama` × `memory`, `redis`), prints a grid, writes `docs/benchmark-baseline.md`.
- Note: real providers require API keys; the matrix is a **pre-release / manual** job, not per-commit. Ollama cell needs a local Ollama; skip gracefully (record `"skipped": true`) if unreachable.

- [ ] **Step 1: Confirm CI layout**

Run: `ls .github/workflows/ 2>/dev/null && ls engine/bench/runs 2>/dev/null || echo "no runs dir yet"`
Expected: shows existing workflows (extend them) and confirms `runs/` absence.

- [ ] **Step 2: Write provider_matrix.py**

```python
# engine/bench/provider_matrix.py
from __future__ import annotations
import argparse
import asyncio
import tempfile
import time
from pathlib import Path

from app.config import Settings
from app.llm.factory import create_llm_provider
from bench.factory import build_bench_orchestrator
from bench.metrics import summarize
from eval.runner import _load_golden_queries, _score_keywords


async def run_provider_cell(provider: str, session_backend: str) -> dict:
    settings = Settings(LLM_PROVIDER=provider)
    try:
        llm = create_llm_provider(settings)
    except Exception as exc:
        return {"provider": provider, "backend": session_backend, "skipped": True, "error": str(exc)}

    tmp = Path(tempfile.mkdtemp())
    orch, audit = build_bench_orchestrator(tmp, llm=llm)
    queries = _load_golden_queries()

    kw_scores: list[float] = []
    e2e: list[float] = []
    costs: list[float] = []
    for gq in queries:
        t0 = time.monotonic()
        try:
            res = await orch.run(gq["query"], session_id=f"pm-{gq['id']}")
            kw_scores.append(_score_keywords(res.spl, gq.get("expected_spl_keywords", [])))
            costs.append(audit.last_cost())
        except Exception:
            kw_scores.append(0.0)
        e2e.append((time.monotonic() - t0) * 1000.0)

    return {
        "provider": provider,
        "backend": session_backend,
        "keyword_pass": sum(1 for s in kw_scores if s >= 0.5) / len(kw_scores),
        "e2e_p95_ms": summarize(e2e).p95,
        "cost_usd_mean": (sum(costs) / len(costs)) if costs else 0.0,
    }


async def _run_matrix(providers, backends) -> list[dict]:
    cells = []
    for p in providers:
        for b in backends:
            cells.append(await run_provider_cell(p, b))
    return cells


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 4 provider/scale matrix")
    parser.add_argument("--providers", default="anthropic,ollama")
    parser.add_argument("--backends", default="memory")
    args = parser.parse_args()
    cells = asyncio.run(_run_matrix(args.providers.split(","), args.backends.split(",")))
    lines = ["| Provider | Backend | KW pass | p95 ms | $/query |",
             "|----------|---------|---------|--------|---------|"]
    for c in cells:
        if c.get("skipped"):
            lines.append(f"| {c['provider']} | {c['backend']} | skipped | — | — |")
        else:
            lines.append(f"| {c['provider']} | {c['backend']} | {c['keyword_pass']:.0%} | "
                         f"{c['e2e_p95_ms']:.0f} | {c['cost_usd_mean']:.4f} |")
    md = "# Setuq benchmark baseline\n\n## Provider / scale matrix\n\n" + "\n".join(lines) + "\n"
    Path(__file__).parents[2].joinpath("docs/benchmark-baseline.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Create runs dir gitignore**

Create `engine/bench/runs/.gitignore`:
```
run-*.json
!baseline.json
```

- [ ] **Step 4: Extend CI**

In the existing workflow (path from Step 1), add a step after the test job:
```yaml
      - name: Bench gates (fast, mocked)
        working-directory: engine
        run: |
          python3 -m pytest tests/bench/ -q
          python3 -m bench.latency_bench --reps 5 --warmup 1 --delay-ms 5 --report
```
(SLO + regression gate enforcement wires `bench.gates` into a follow-up `--gate` flag on `latency_bench` once baseline thresholds are set from the first real run; document the chosen thresholds in `docs/benchmark-baseline.md`.)

- [ ] **Step 5: Verify the full bench suite is green**

Run: `cd engine && python3 -m pytest tests/bench/ -q`
Expected: PASS (all bench tests).

- [ ] **Step 6: Generate the baseline (manual, needs a key or Ollama)**

Run: `cd engine && python3 -m bench.provider_matrix --providers anthropic --backends memory`
Expected: writes `docs/benchmark-baseline.md` with a one-row matrix. If no key, the cell shows `skipped` — commit the skeleton anyway.

- [ ] **Step 7: Commit**

```bash
git add engine/bench/provider_matrix.py engine/bench/runs/.gitignore docs/benchmark-baseline.md .github/workflows/
git commit -m "feat(bench): phase 4 provider matrix + CI gate wiring + baseline"
```

---

## Self-Review

**Spec coverage** (vs `docs/benchmarking-plan.md`):
- Latency (per-stage p50/p95/p99) → Tasks 1,2,6. ✓
- Cost ($/query, per-run) → Tasks 4,6 (per-run total; per-stage explicitly excluded with reason). ✓
- Throughput/load → Task 9. ✓
- Resilience/fault injection → Task 8 (llm_error, guardrail_block, splunk_down; timeout path via `FaultyMockLLM("timeout")` available for extension). ✓
- Provider/scale matrix → Task 10. ✓
- CI regression gates → Tasks 7,10. ✓
- Reuse eval harness / OTel / LLMUsage / MockLLM → factory + capture + mocks all import existing code. ✓

**Known gaps to flag to the user (not silently dropped):**
1. **Per-stage cost** is not implemented (concurrent stages + process-global budget contextvar make it unattributable). Plan measures per-run cost only.
2. **Resilience Task 8 Step 4** has a real risk: `MockLLM` returns a *fixed* SPL regardless of query, so a "DELETE" natural-language query may not produce guardrail-tripping SPL. The step documents the fallback (drive the guardrail with a known-bad SPL directly). The implementer must verify against `config/guardrails.yaml` — do not assume.
3. **SLO threshold numbers** (`e2e_p95_ms=6000`, `guardrail_p95_ms=5`, `cost_usd_mean`) are placeholders in `check_slo` callers; real values must be set from the first baseline run before the gate is made blocking. Until then the gate runs in report-only mode.
4. **`LLM_PROVIDER=stub`** in Task 9 assumes a stub provider exists; if not, the load README must start the app pointed at a local Ollama or a mock. Verify `app/llm/factory.py` provider names during Task 9.

**Placeholder scan:** every code step contains real code; no TBD/TODO. ✓
**Type consistency:** `LatencySummary`, `GateResult`, `FaultOutcome`, `build_bench_orchestrator` signature, and the run-payload shape (`stages`/`e2e`/`cost_usd_mean`) are used identically across Tasks 5–10. ✓
