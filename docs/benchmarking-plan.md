# Setuq Benchmarking Plan

Benchmark the agentic pipeline (`plan → SPL → guardrail → execute → summarize/analyze → actions → decide → audit`) as **both** an LLM agent (quality, safety, cost) and a **distributed microservice** (latency, throughput, resilience, scale).

Existing `eval/runner.py` already scores per-query quality/safety. This plan reuses it as the quality layer and adds the five layers it does not cover: **latency, cost, load, resilience, provider/scale matrix** — plus CI regression gates.

---

## 1. Goals & Non-Goals

**Goals**
- Numbers that gate releases (SLOs), not vanity metrics.
- Attribute cost/latency **per pipeline stage** — know which agent is the bottleneck.
- Prove the three moats with data: sovereign (Ollama parity), closed-loop (decision precision), Splunk-agnostic (client abstraction overhead ≈ 0).
- Reproducible: fixed seeds, pinned judge model, deterministic mock mode.

**Non-Goals**
- Not benchmarking Splunk itself. `SplunkClient` is mocked/stubbed for perf runs so we measure the *agent*, not the backend.
- No live-fire against production Splunk in load tests.

---

## 2. Benchmark Dimensions

| # | Dimension | Question it answers | Layer |
|---|-----------|--------------------|-------|
| Q | **Quality/Correctness** | Does SPL answer the question? | existing `runner.py` |
| S | **Safety/Guardrail** | Are dangerous/injection queries blocked? | existing (categories: guardrail, injection, ambiguous) |
| L | **Latency** | How fast, per stage, p50/p95/p99? | new |
| C | **Cost** | Tokens & $ per query, per stage | new |
| T | **Throughput** | Queries/sec at N concurrent, saturation point | new |
| R | **Resilience** | Behavior under LLM/Splunk failure, timeout, circuit-break | new |
| M | **Provider/Scale matrix** | Anthropic vs OpenAI vs Ollama; in-mem vs Redis | new |

---

## 3. Datasets

- **Quality/Safety**: existing `eval/golden_queries.jsonl` (40 queries: easy, multi_step, guardrail, unknown_index, ambiguous, injection). Grow to ≥100 for statistical power; keep category balance.
- **Load profile**: synthetic mix weighted to production shape — ~70% easy, 20% multi_step, 10% guardrail/injection. Multi-step is the expensive path (more LLM calls) so it must be represented, not averaged away.
- **Determinism**: `MockLLM` (from `tests/pipeline/test_orchestrator.py`) for latency/throughput/resilience runs — removes LLM variance so we measure *pipeline overhead*. Real providers only for quality + provider-matrix + cost runs.

---

## 4. Metrics & Instrumentation

Instrumentation already exists — **reuse it, don't rebuild**:
- **Per-stage timing**: OTel spans (`app/observability/tracer.py`) already wrap stages. Export to collector, aggregate p50/p95/p99 per span name.
- **Cost/tokens**: `LLMUsage` on `LLMResponse` (`app/llm/base.py`); budget contextvar in `harness.py`. Sum per stage per request.
- **Quality**: `EvalResult` dataclass (keyword_score, spl_quality{validity,groundedness,time_hygiene}, judge_rubric, decision_precision).

**Latency** — measure per stage AND end-to-end:
```
planning | spl | guardrail | executing | analyzing | deciding | audit | E2E
```
Report p50/p95/p99 + max. Guardrail/audit should be sub-ms (no LLM); planning/spl/analysis/decision are LLM-bound.

**Cost** — tokens_in, tokens_out, $ per stage; $ per query (mean + p95). Flag multi_step queries separately (they fan out).

**Throughput** — queries/sec sustained, error rate, latency degradation curve vs concurrency (1, 5, 10, 25, 50, 100). Find the knee.

**Resilience** — recovery time, % requests served (degraded vs failed), circuit-breaker open/close correctness.

---

## 5. Methodology per Layer

### 5.1 Latency & Cost (single-request, real providers)
- Warm run (idempotency cache OFF, `IDEMPOTENCY_CACHE_ENABLED=false`) — measure worst case, not cache hits.
- Separate run with cache ON to quantify cache benefit.
- N=50 per query category, discard first 5 (warmup), report percentiles.
- Break down by stage via OTel spans. Deliverable: **stacked latency + cost bar per category**.

### 5.2 Throughput / Load (MockLLM, deterministic)
- Tool: `locust` or `k6` against `/api/query` and `/api/query/stream` (SSE).
- Inject fixed per-call LLM latency in MockLLM (e.g. 200ms) to simulate real provider without variance.
- Ramp concurrency 1→100. Record RPS, p95 latency, error %, CPU/mem.
- **Rate-limiter interaction**: run once with `RATE_LIMIT_ENABLED=false` (raw capacity) and once true (verify 60/min-IP + 10/min-session enforced, correct 429s).
- SSE: verify step events stream in order under load, no dropped `done`.

### 5.3 Resilience / Fault Injection
Drive via config + fault-injecting mock providers:
| Fault | Inject how | Expected behavior |
|-------|-----------|-------------------|
| LLM timeout | mock sleeps > timeout | `HarnessedProvider` `asyncio.wait_for` fires → retry |
| LLM hard fail | mock raises | tenacity retry → then `FallbackProvider` chain |
| All providers fail | all mocks raise | `RuntimeError("All N LLM providers failed")`, clean 5xx, audited |
| Budget exceeded | tiny budget contextvar | request aborts, no runaway spend (see `tests/adversarial/budget_enforcement`) |
| Splunk down | `SplunkClient` raises | pipeline degrades, guardrail/plan still audited |
| OTel collector down | bad `OTLP_ENDPOINT` | `_CircuitBreakingExporter` opens after 5 fails, pipeline unblocked (Invariant 7) |
| Unknown index | query gq-022..025 | reactive refresh task fires, non-blocking (Invariant 9) |
| Partial gather fail | one step of multi-step fails | see `tests/adversarial/gather_partial_failure` |

Measure: does the system stay up, degrade gracefully, and **audit the failure**? Zero silent drops except documented ones (Langfuse `QueueFull`, Invariant 8).

### 5.4 Provider & Scale Matrix (the moat proof)
Run full quality suite × provider × session-backend:

| Axis | Values |
|------|--------|
| Provider | anthropic (opus/sonnet/haiku), openai-compat, **ollama (local)** |
| Sessions | in-memory `SessionManager` vs `RedisSessionManager` |

Report per cell: quality (keyword/judge/decision precision), p95 latency, $/query. **Sovereign story = Ollama column**: quantify quality gap vs cloud and the latency/cost tradeoff. Redis vs in-mem = horizontal-scale readiness (does shared state cost latency?).

---

## 6. SLOs / Pass Gates (proposed — tune after baseline)

| Metric | Target |
|--------|--------|
| Guardrail block rate (guardrail+injection categories) | **100%** (zero bypass — hard gate) |
| Ambiguous → escalate | 100% (never invent SPL) |
| Keyword pass (easy) | ≥ 90% |
| Judge correctness (easy+multi_step) | ≥ 4.0/5 |
| Decision precision | ≥ 0.85 |
| E2E p95 latency (easy, cloud) | ≤ 6 s |
| Guardrail stage latency | ≤ 5 ms |
| $/query (easy, primary provider) | ≤ target $ (set from baseline) |
| Sustained RPS @ p95<SLO | ≥ baseline (record, gate on regression) |
| Error rate under 50 concurrent | < 1% |

Safety gates are **hard** (block release). Perf/cost gates are **regression** (block if worse than previous run beyond threshold).

---

## 7. Regression / CI Integration

- `runner.py --report` already persists runs + emits trend table with **prompt-hash change attribution** (a delta ties to a prompt version bump). Extend the persisted `EvalResult` to also store per-stage latency + cost so the trend table catches perf/cost regressions, not just quality.
- CI job (nightly + on prompt/guardrail change): run quality suite (MockLLM for determinism where possible, real for a small judged sample), fail on safety regression or metric drop > threshold (`_REGRESSION_THRESHOLD`).
- Load + resilience: weekly or pre-release (heavier, not per-commit).
- Push all runs to Langfuse (`--langfuse`) for longitudinal dashboards.

---

## 8. Deliverables

1. `bench/` module: `latency_bench.py`, `load/` (locust/k6 scripts), `resilience_bench.py`, `provider_matrix.py`.
2. Extended `EvalResult` with latency + cost fields; extended trend report.
3. Baseline report: `docs/benchmark-baseline.md` (numbers as of first run).
4. Langfuse dashboard + CI gate config.

---

## 9. Phasing

```
Phase 1  Latency + cost instrumentation (reuse OTel/LLMUsage) → per-stage baseline   → verify: stacked latency/cost chart
Phase 2  Load harness (locust/k6, MockLLM)                    → RPS + knee            → verify: degradation curve, 429 correctness
Phase 3  Resilience/fault injection                           → resilience matrix     → verify: every fault row audited, no silent drop
Phase 4  Provider/scale matrix                                → moat proof            → verify: Ollama column quality gap quantified
Phase 5  CI gates + Langfuse dashboards                       → regression protection → verify: seeded regression trips the gate
```

---

## Open Questions (need input)

1. Target **primary provider + model** for headline SLOs? (opus vs sonnet vs haiku changes cost/latency targets a lot.)
2. Load target: real expected **peak concurrency / RPS**? Sets the throughput gate.
3. Is **Ollama sovereign parity** a release gate or a directional metric? (Decides how hard we chase the gap.)
4. `k6` or `locust`? (k6 = better SSE, JS scripts; locust = Python, reuses our fixtures.)
