# Design: Summary-Index Guardrail + SPL-Match Confidence

Date: 2026-07-26
Status: Approved (design phase)

## Goals

1. **Guardrail**: block agent-generated SPL from writing to summary indexes
   (`| collect`, `si*` family). `delete` and `outputlookup` are already blocked.
2. **Confidence**: show a per-query confidence score. Two distinct scores:
   - **SPL match** (new): how well the generated SPL answers the NL question
     (pre-execution intent). Produced by a new LLM scoring agent.
   - **Decision** (existing): confidence in the investigation conclusion
     (post-analysis). Already in the response; currently unrendered in the UI.

Non-goals: changing DecisionEngine logic; auto-execute thresholds; heuristic
scoring.

---

## Feature 1 — Guardrail: block summary-indexing

`config/guardrails.yaml` is the sole source of truth for resource-heavy patterns
(no code defaults). Changes are YAML-only:

- **Replace** the existing `collect without index=` rule with a **blanket collect
  block**:
  - pattern: `\|\s*collect\b`
  - reason: `collect / summary-index write not allowed via agent`
  - Rationale: summary indexing is done via `| collect index=summary`. The only
    way to catch it is to block all `| collect`. The agent runs read-only SOC
    queries and has no legitimate `collect` use.
- **Add** the summary-indexing command family (`si*`):
  - pattern: `\|\s*si(stats|chart|timechart|top|rare)\b`
  - reason: `summary-indexing command (si*) not allowed via agent`

No change to `guardrails.py` logic. `_check_index` already skips `collect`
stages from the unknown-index check; those stages are now caught by the
resource-heavy check instead. `delete` and `outputlookup` patterns stay as-is.

Optional cleanup (flagged, not required): sync the dead `_RESOURCE_HEAVY_PATTERNS`
constant in `guardrails.py` (lines ~37-46) so its documented denylist matches the
YAML. The constant is unused (YAML wins), but it currently omits summary-index
patterns and could mislead readers.

### Tests (Feature 1)

Extend the guardrail test suite (`tests/pipeline/test_guardrails*` /
`test_guardrail_labeled`):

- **Blocked** (raises `GuardrailViolation`): `| collect index=summary`,
  `| sistats count`, `| sichart count`, `| sitimechart count`, `| sitop src`,
  `| sirare src`.
- **Still blocked**: `| delete`, `| outputlookup foo`.
- Sanity: a normal read-only query (e.g. `index=main earliest=-24h | stats count`)
  still passes.

---

## Feature 2 — SPL-match confidence

### New agent: `app/pipeline/spl_confidence.py`

```python
class _ConfidenceSchema(BaseModel):
    confidence: float = 0.0
    reasoning: str = ""

class SPLConfidenceScorer:
    def __init__(self, llm: LLMProvider): ...

    async def score(self, query: str, spl: str, schema_context: str) -> float | None:
        """Rate 0.0-1.0 how well the SPL answers the NL query. Clamped.
        Returns None on validation failure (never raises into the pipeline)."""
```

- Uses `generate_validated` with a new prompt `spl_confidence`.
- Clamp result to `[0.0, 1.0]`.
- On `LLMOutputValidationError` → return `None`. `None` (not `0.0`) so the UI can
  render "n/a" and not conflate a scoring failure with a genuinely bad SPL.

### Prompt

Add `spl_confidence` to `config/prompts.yaml` and to `REQUIRED_PROMPTS` in
`prompt_registry.py` (fail-closed: missing prompt aborts startup). Prompt asks
the model to return `{confidence: 0.0-1.0, reasoning: str}` given the user
question, the generated SPL, and the schema context.

### Orchestrator wiring (`orchestrator.py`)

- Add `spl_confidence: float | None = None` to the `PipelineResult` dataclass.
- Add an optional constructor param `spl_confidence_scorer: SPLConfidenceScorer |
  None = None` (default `None` → scoring skipped, `spl_confidence` stays `None`;
  keeps existing test factories green).
- Run the scorer inside the **existing step-5 `TaskGroup`** (currently runs
  `execute_spl` + `explain` concurrently, ~line 353). The scorer only needs
  `query`, `spl`, and `schema_context`, so it runs in parallel with the Splunk
  execution — **zero added wall-clock latency**. Guard with `if self._scorer is
  not None`.
- Populate `PipelineResult.spl_confidence` from the task result.

### API (`schemas.py`, `routes.py`)

- `QueryResponse.spl_confidence: float | None = None`.
- `routes.py` non-streaming builder maps `spl_confidence=result.spl_confidence`.
- The UI's data path is the non-streaming `POST /api/query` (`sendQuery` →
  `message.data`); SSE `/api/query/stream` is progress-only (its `done` event
  carries just `spl`). So the field only needs to be on the `QueryResponse`
  returned by `POST /api/query`. No SSE change required.

### Startup (`main.py`)

Construct `SPLConfidenceScorer(llm_provider)` and pass it to
`PipelineOrchestrator`.

### UI

- `ui/src/api/client.ts`: add `spl_confidence: number | null` to `QueryResponse`.
- `ui/src/components/MessageBubble.tsx`: render two distinct badges in the message
  footer:
  - **SPL match {n}%** — from `spl_confidence` (render "n/a" when `null`).
  - **Decision {n}%** — from `decision.confidence_score` (currently unrendered).
  - Distinct labels because they mean different things.
- `ui/src/components/QueryDetails.tsx`: show the SPL-match % adjacent to the SPL
  block.

### Tests (Feature 2)

- `tests/pipeline/test_spl_confidence.py`: valid parse → float; out-of-range →
  clamped; `LLMOutputValidationError` → `None`.
- Orchestrator test: `spl_confidence` populated when scorer wired; `None` when the
  scorer param is absent.
- API test: `spl_confidence` present in the `/api/query` response body.

---

## Semantics recap (for UI copy)

- **SPL match** = confidence the *query* is correct (pre-execution intent).
- **Decision** = confidence in the *investigation conclusion* (post-analysis).

## Success criteria

- Guardrail blocks `collect` + `si*` (tests green); delete/outputlookup unaffected.
- `/api/query` and SSE `done` return `spl_confidence`.
- UI shows both scores with distinct labels; SPL-match shows "n/a" on scoring
  failure.
- Existing test suite stays green (scorer optional).
