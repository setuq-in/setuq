# Summary-Index Guardrail + SPL-Match Confidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Block agent-generated SPL from writing to summary indexes, and add a per-query "SPL match" confidence score alongside the existing "Decision" confidence.

**Architecture:** Feature 1 is a pure `config/guardrails.yaml` edit (YAML is the sole source of truth for guardrail patterns). Feature 2 adds an isolated `SPLConfidenceScorer` LLM agent that runs in the orchestrator's existing step-5 `TaskGroup` (parallel with Splunk execution → zero added latency); its `float | None` result flows through `PipelineResult` → `QueryResponse` → the React UI, shown as a distinct badge from the pre-existing `decision.confidence_score`.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic, pytest/pytest-asyncio; React 19 + TypeScript + Vite (UI).

## Global Constraints

- Guardrail patterns live ONLY in `engine/config/guardrails.yaml` — no code defaults. Tests read the shipped YAML via the `shipped_guardrail_config` fixture (`engine/tests/conftest.py`).
- Pipeline prompts live ONLY in `engine/config/prompts.yaml`; required prompts are listed in `REQUIRED_PROMPTS` (`app/pipeline/prompt_registry.py`) and fail-closed at startup. Agents call `prompt_registry.get(name)`.
- Never catch bare `Exception` in pipeline steps. The scorer catches ONLY `LLMOutputValidationError`.
- Agents get their LLM via constructor `llm: LLMProvider`. Structured LLM output uses `generate_validated(llm, system_prompt, history, user_prompt, model_class)` from `app.pipeline.llm_utils`.
- Run backend tests from `engine/`: `python -m pytest tests/ -q`.
- Run UI typecheck+build from `ui/`: `npm run build`.

---

### Task 1: Guardrail — block summary-indexing (`collect` + `si*`)

**Files:**
- Modify: `engine/config/guardrails.yaml` (resource_heavy_patterns list)
- Test: `engine/tests/pipeline/test_guardrails.py`

**Interfaces:**
- Consumes: `QueryGuardrail.validate(spl) -> GuardrailResult` (raises `GuardrailViolation`); `shipped_guardrail_config` fixture.
- Produces: nothing consumed by later tasks (self-contained).

- [ ] **Step 1: Write the failing tests**

Add to `engine/tests/pipeline/test_guardrails.py` (uses the existing `guardrail` fixture at the top of that file, which is built from `shipped_guardrail_config`):

```python
import pytest
from app.pipeline.guardrails import GuardrailViolation


@pytest.mark.parametrize("spl", [
    'index=main earliest=-1d | collect index=summary',
    'index=main earliest=-1d | sistats count by host',
    'index=main earliest=-1d | sichart count',
    'index=main earliest=-1d | sitimechart count',
    'index=main earliest=-1d | sitop src_ip',
    'index=main earliest=-1d | sirare src_ip',
])
def test_summary_index_commands_blocked(guardrail, spl):
    with pytest.raises(GuardrailViolation):
        guardrail.validate(spl)


@pytest.mark.parametrize("spl", [
    'index=main earliest=-1d | delete',
    'index=main earliest=-1d | outputlookup exfil.csv',
])
def test_delete_and_outputlookup_still_blocked(guardrail, spl):
    with pytest.raises(GuardrailViolation):
        guardrail.validate(spl)


def test_readonly_query_still_passes(guardrail):
    spl = 'index=main earliest=-1d | stats count by host'
    assert guardrail.validate(spl).passed
```

- [ ] **Step 2: Run tests to verify the summary-index cases fail**

Run: `python -m pytest tests/pipeline/test_guardrails.py -q`
Expected: `test_summary_index_commands_blocked` FAILS for the `collect index=summary`, `sistats`, `sichart`, `sitimechart`, `sitop`, `sirare` cases (no matching pattern yet). The delete/outputlookup and read-only cases PASS.

- [ ] **Step 3: Update the YAML patterns**

In `engine/config/guardrails.yaml`, under `resource_heavy_patterns`, REPLACE this entry:

```yaml
  - pattern: '\|\s*collect\b(?!.*index=)'
    reason: "collect without target index"
```

with these two entries (blanket collect block + si* family):

```yaml
  - pattern: '\|\s*collect\b'
    reason: "collect / summary-index write not allowed via agent"
  - pattern: '\|\s*si(stats|chart|timechart|top|rare)\b'
    reason: "summary-indexing command (si*) not allowed via agent"
```

Leave the `join`, `delete`, and `outputlookup` entries unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/pipeline/test_guardrails.py -q`
Expected: PASS (all cases).

- [ ] **Step 5: Run the full guardrail + orchestrator suites (regression check)**

Run: `python -m pytest tests/pipeline/test_guardrails.py tests/pipeline/test_guardrails_hardening.py tests/pipeline/test_guardrail_labeled.py tests/pipeline/test_orchestrator.py -q`
Expected: PASS. (The MockLLM SPL in `test_orchestrator.py` is `index=chocolate_index ... | stats ...` — no `collect`, so blanket collect block does not affect it.)

- [ ] **Step 6: Commit**

```bash
git add engine/config/guardrails.yaml engine/tests/pipeline/test_guardrails.py
git commit -m "feat(guardrails): block summary-index writes (collect + si* commands)"
```

---

### Task 2: `SPLConfidenceScorer` agent + prompt

**Files:**
- Create: `engine/app/pipeline/spl_confidence.py`
- Modify: `engine/config/prompts.yaml` (add `spl_confidence` prompt)
- Modify: `engine/app/pipeline/prompt_registry.py` (add `spl_confidence` to `REQUIRED_PROMPTS`)
- Test: `engine/tests/pipeline/test_spl_confidence.py`

**Interfaces:**
- Consumes: `generate_validated`, `LLMOutputValidationError` from `app.pipeline.llm_utils`; `prompt_registry.get`; `LLMProvider`.
- Produces: `class SPLConfidenceScorer` with `def __init__(self, llm: LLMProvider)` and `async def score(self, query: str, spl: str, schema_context: str) -> float | None`. Later tasks (orchestrator) construct and call it.

- [ ] **Step 1: Write the failing test**

Create `engine/tests/pipeline/test_spl_confidence.py`:

```python
import pytest
from app.llm.base import LLMProvider, LLMResponse, LLMUsage
from app.pipeline.spl_confidence import SPLConfidenceScorer


def _usage():
    return LLMUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2)


class _FixedLLM(LLMProvider):
    def __init__(self, content):
        self._content = content

    async def generate(self, system_prompt, history, user_prompt):
        return LLMResponse(content=self._content, usage=_usage())


@pytest.mark.asyncio
async def test_score_returns_float():
    scorer = SPLConfidenceScorer(llm=_FixedLLM('{"confidence": 0.82, "reasoning": "ok"}'))
    score = await scorer.score(query="q", spl="index=main earliest=-1d", schema_context="")
    assert score == pytest.approx(0.82)


@pytest.mark.asyncio
async def test_score_clamps_out_of_range():
    scorer = SPLConfidenceScorer(llm=_FixedLLM('{"confidence": 1.7, "reasoning": "x"}'))
    assert await scorer.score(query="q", spl="s", schema_context="") == 1.0
    scorer_low = SPLConfidenceScorer(llm=_FixedLLM('{"confidence": -0.5, "reasoning": "x"}'))
    assert await scorer_low.score(query="q", spl="s", schema_context="") == 0.0


@pytest.mark.asyncio
async def test_score_returns_none_on_invalid_output():
    # Non-JSON, unrecoverable output -> generate_validated raises
    # LLMOutputValidationError after its retry -> scorer returns None.
    scorer = SPLConfidenceScorer(llm=_FixedLLM('not json at all'))
    assert await scorer.score(query="q", spl="s", schema_context="") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/pipeline/test_spl_confidence.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.pipeline.spl_confidence'`.

- [ ] **Step 3: Add the prompt to `prompts.yaml`**

Append under the top-level `prompts:` mapping in `engine/config/prompts.yaml` (match the 2-space indent + `|` block style of the other entries, e.g. `decision_engine`):

```yaml
  spl_confidence: |
    You rate how well a generated Splunk SPL query answers a user's natural-language security question. You are given the user question, the generated SPL, and the available schema context.

    Rules:
    - Return ONLY a JSON object (no markdown fences) with:
      - "confidence": float 0.0-1.0 — how confident you are that running this SPL correctly answers the question
      - "reasoning": string — 1-2 sentences explaining the score
    - Lower the score when: the SPL targets the wrong index/fields, ignores part of the question, is overly broad, or uses fields absent from the schema context.
    - Raise the score when the SPL precisely and completely expresses the question against valid schema fields.
    - This measures query intent only — do NOT judge the results (you cannot see them).
```

- [ ] **Step 4: Register the prompt as required**

In `engine/app/pipeline/prompt_registry.py`, add `"spl_confidence"` to the `REQUIRED_PROMPTS` frozenset:

```python
REQUIRED_PROMPTS: frozenset[str] = frozenset(
    {
        "spl_generator",
        "spl_explain",
        "summarizer",
        "action_suggester",
        "analysis_agent",
        "planner",
        "decision_engine",
        "spl_confidence",
    }
)
```

- [ ] **Step 5: Write the agent**

Create `engine/app/pipeline/spl_confidence.py`:

```python
import logging

from pydantic import BaseModel

from app.llm.base import LLMProvider
from app.pipeline import prompt_registry
from app.pipeline.llm_utils import generate_validated, LLMOutputValidationError

_logger = logging.getLogger("setuq.spl_confidence")


class _ConfidenceSchema(BaseModel):
    confidence: float = 0.0
    reasoning: str = ""


class SPLConfidenceScorer:
    """Rates how well a generated SPL answers the user's NL question (0.0-1.0)."""

    def __init__(self, llm: LLMProvider):
        self._llm = llm

    async def score(self, query: str, spl: str, schema_context: str) -> float | None:
        """Return a clamped 0.0-1.0 confidence, or None if scoring failed."""
        user_prompt = (
            f"User question: {query}\n\n"
            f"Generated SPL: {spl}\n\n"
            f"Schema context:\n{schema_context}"
        )
        try:
            data = await generate_validated(
                llm=self._llm,
                system_prompt=prompt_registry.get("spl_confidence"),
                history=[],
                user_prompt=user_prompt,
                model_class=_ConfidenceSchema,
            )
        except LLMOutputValidationError:
            _logger.warning("spl_confidence scoring failed — returning None")
            return None
        return max(0.0, min(1.0, data.confidence))
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/pipeline/test_spl_confidence.py -q`
Expected: PASS (3 tests).

- [ ] **Step 7: Verify prompt-registry completeness still holds**

Run: `python -m pytest tests/pipeline/test_prompt_registry.py -q`
Expected: PASS (the new required prompt exists in the shipped YAML, so `ensure_complete()` is satisfied).

- [ ] **Step 8: Commit**

```bash
git add engine/app/pipeline/spl_confidence.py engine/config/prompts.yaml engine/app/pipeline/prompt_registry.py engine/tests/pipeline/test_spl_confidence.py
git commit -m "feat(pipeline): add SPLConfidenceScorer agent + spl_confidence prompt"
```

---

### Task 3: Orchestrator integration

**Files:**
- Modify: `engine/app/pipeline/orchestrator.py` (`PipelineResult` dataclass ~line 61; `PipelineOrchestrator.__init__` ~line 77; step-5 `TaskGroup` ~line 353; both `PipelineResult(...)` constructions ~line 475 and ~line 486)
- Test: `engine/tests/pipeline/test_orchestrator.py`

**Interfaces:**
- Consumes: `SPLConfidenceScorer.score(query, spl, schema_context) -> float | None` from Task 2.
- Produces: `PipelineResult.spl_confidence: float | None`; `PipelineOrchestrator.__init__` gains `spl_confidence_scorer: SPLConfidenceScorer | None = None`.

- [ ] **Step 1: Write the failing tests**

Add to `engine/tests/pipeline/test_orchestrator.py` (note `_build_orchestrator` and `MockLLM` already exist in this file; `PipelineResult` is already imported):

```python
from app.pipeline.spl_confidence import SPLConfidenceScorer


@pytest.mark.asyncio
async def test_spl_confidence_none_when_scorer_absent(orchestrator):
    result = await orchestrator.run("Show me total revenue by store")
    assert result.spl_confidence is None


@pytest.mark.asyncio
async def test_spl_confidence_populated_when_scorer_wired(tmp_path):
    class _ScoreLLM(MockLLM):
        async def generate(self, system_prompt, history, user_prompt):
            if "rate how well a generated Splunk SPL" in system_prompt:
                from app.llm.base import LLMResponse
                return LLMResponse(content='{"confidence": 0.73, "reasoning": "ok"}',
                                   usage=_mock_usage())
            return await super().generate(system_prompt, history, user_prompt)

    llm = _ScoreLLM()
    orch = _build_orchestrator(tmp_path, llm=llm)
    orch._spl_confidence_scorer = SPLConfidenceScorer(llm=llm)
    result = await orch.run("Show me total revenue by store")
    assert result.spl_confidence == pytest.approx(0.73)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/pipeline/test_orchestrator.py -k spl_confidence -q`
Expected: FAIL with `AttributeError: 'PipelineResult' object has no attribute 'spl_confidence'`.

- [ ] **Step 3: Add the `PipelineResult` field**

In `engine/app/pipeline/orchestrator.py`, add to the `PipelineResult` dataclass (after `chart_specs`):

```python
    chart_spec: "ChartSpec | None" = None
    chart_specs: list = field(default_factory=list)
    spl_confidence: float | None = None
```

- [ ] **Step 4: Add the constructor param + store it**

In `PipelineOrchestrator.__init__`, add the parameter (after `chart_inferer`):

```python
        chart_inferer: ChartInferer | None = None,
        spl_confidence_scorer: "SPLConfidenceScorer | None" = None,
    ):
```

and store it alongside the other assignments (near `self._chart_inferer = chart_inferer`):

```python
        self._spl_confidence_scorer = spl_confidence_scorer
```

Add the import at the top of the file (with the other pipeline imports):

```python
from app.pipeline.spl_confidence import SPLConfidenceScorer
```

- [ ] **Step 5: Score inside the step-5 TaskGroup**

In the `pipeline.execute_and_explain` block (~line 353), add a third concurrent task guarded by the scorer's presence. Replace:

```python
                    async with asyncio.TaskGroup() as tg:
                        execute_t = tg.create_task(
                            self._splunk_client.execute_spl(spl_result.spl)
                        )
                        explain_t = tg.create_task(
                            self._spl_generator.explain(spl_result.spl)
                        )
```

with:

```python
                    confidence_t = None
                    async with asyncio.TaskGroup() as tg:
                        execute_t = tg.create_task(
                            self._splunk_client.execute_spl(spl_result.spl)
                        )
                        explain_t = tg.create_task(
                            self._spl_generator.explain(spl_result.spl)
                        )
                        if self._spl_confidence_scorer is not None:
                            confidence_t = tg.create_task(
                                self._spl_confidence_scorer.score(
                                    query=query, spl=spl_result.spl, schema_context=schema_context
                                )
                            )
```

Then, immediately after the `except*` handler for that TaskGroup (after `spl_result = _SPLResult(...)` is rebuilt, ~line 369), capture the score:

```python
                spl_confidence = confidence_t.result() if confidence_t is not None else None
```

- [ ] **Step 6: Thread the value into both `PipelineResult(...)` returns**

There are two `PipelineResult(...)` constructions (~line 475 and ~line 486). Add `spl_confidence=spl_confidence,` to BOTH. If one construction is on a path that runs before `spl_confidence` is assigned, verify by reading the surrounding code; both `PipelineResult` builds in `run()`/`run_streaming()` occur after step 5, so `spl_confidence` is in scope. Example:

```python
            return PipelineResult(
                query=query,
                spl=spl_result.spl,
                spl_explanation=spl_result.explanation,
                ...
                chart_specs=chart_specs,
                spl_confidence=spl_confidence,
            )
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `python -m pytest tests/pipeline/test_orchestrator.py -q`
Expected: PASS (new `spl_confidence` tests + all existing orchestrator tests).

- [ ] **Step 8: Commit**

```bash
git add engine/app/pipeline/orchestrator.py engine/tests/pipeline/test_orchestrator.py
git commit -m "feat(pipeline): wire SPLConfidenceScorer into orchestrator (parallel, optional)"
```

---

### Task 4: API surface + startup wiring

**Files:**
- Modify: `engine/app/api/schemas.py` (`QueryResponse`)
- Modify: `engine/app/api/routes.py` (`QueryResponse(...)` builder ~line 159)
- Modify: `engine/app/main.py` (construct scorer ~line 139, pass to orchestrator ~line 174)
- Test: `engine/tests/api/test_api.py`

**Interfaces:**
- Consumes: `PipelineResult.spl_confidence` (Task 3); `SPLConfidenceScorer` (Task 2).
- Produces: `QueryResponse.spl_confidence: float | None` in the `POST /api/query` response body.

- [ ] **Step 1: Write the failing test**

Add to `engine/tests/api/test_api.py`. The `client` fixture is an httpx `AsyncClient` and tests are async (`@pytest.mark.asyncio`, `await client.post(...)`) — mirror `test_post_query` (line 81). The `mock_orchestrator` fixture returns a `PipelineResult` built without `spl_confidence`; after Task 3 the field defaults to `None`. To assert a concrete value flows through, override `orch.run` on the mock:

```python
@pytest.mark.asyncio
async def test_query_response_includes_spl_confidence(client, mock_orchestrator):
    base = await mock_orchestrator.run()          # the fixture's PipelineResult
    base.spl_confidence = 0.77
    mock_orchestrator.run = AsyncMock(return_value=base)
    resp = await client.post("/api/query", json={"query": "show revenue by store"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["spl_confidence"] == 0.77
```

(`AsyncMock` is already imported in `test_api.py`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/api/test_api.py::test_query_response_includes_spl_confidence -q`
Expected: FAIL — `"spl_confidence" not in body` (field absent from schema).

- [ ] **Step 3: Add the field to the response schema**

In `engine/app/api/schemas.py`, add to `QueryResponse` (after `chart_specs`):

```python
    chart_specs: list[ChartSpec] = []         # all requested; >1 -> UI dropdown
    spl_confidence: float | None = None
    message_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
```

- [ ] **Step 4: Map it in the route builder**

In `engine/app/api/routes.py`, in the `QueryResponse(...)` construction (~line 159), add (near `chart_specs=result.chart_specs,`):

```python
            chart_specs=result.chart_specs,
            spl_confidence=result.spl_confidence,
```

- [ ] **Step 5: Construct + wire the scorer in `main.py`**

In `engine/app/main.py`, add the import near the other pipeline imports:

```python
from app.pipeline.spl_confidence import SPLConfidenceScorer
```

Construct it beside the other agents (after `decision_engine = DecisionEngine(llm=llm)`):

```python
    spl_confidence_scorer = SPLConfidenceScorer(llm=llm)
```

Pass it into the orchestrator (in the `PipelineOrchestrator(...)` call, after `chart_inferer=chart_inferer,`):

```python
        chart_inferer=chart_inferer,
        spl_confidence_scorer=spl_confidence_scorer,
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/api/test_api.py -q`
Expected: PASS.

- [ ] **Step 7: Run the full backend suite (regression)**

Run: `python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add engine/app/api/schemas.py engine/app/api/routes.py engine/app/main.py engine/tests/api/test_api.py
git commit -m "feat(api): expose spl_confidence in query response + wire scorer at startup"
```

---

### Task 5: UI — show both confidence badges

**Files:**
- Modify: `ui/src/api/client.ts` (`QueryResponse` interface)
- Modify: `ui/src/components/MessageBubble.tsx` (footer badges)
- Modify: `ui/src/components/QueryDetails.tsx` (SPL-match badge in the GENERATED SPL header)

**Interfaces:**
- Consumes: `QueryResponse.spl_confidence: number | null` and `decision.confidence_score: number` (already typed) from the JSON returned by `sendQuery`.
- Produces: rendered badges. No downstream consumers.

- [ ] **Step 1: Add the field to the TS type**

In `ui/src/api/client.ts`, add to the `QueryResponse` interface (after `chart_specs?`):

```typescript
  chart_specs?: ChartSpec[];
  spl_confidence: number | null;
}
```

- [ ] **Step 2: Render both badges in the message footer**

In `ui/src/components/MessageBubble.tsx`, the footer is the `Bubble footer info` block (~line 201). `decision` is already in scope (`const decision = message.data.decision;`). Add a small helper above the `return` and render two badges. Insert this pct helper near the other `const` declarations (after `const decision = ...`):

```tsx
  const pct = (v: number | null | undefined) =>
    v === null || v === undefined ? 'n/a' : `${Math.round(v * 100)}%`;
```

Then, inside the footer `div` (the one with `flex justify-between items-center ... mt-2`), replace the left `<span>SETUQ AGENT ...</span>` block's sibling structure by adding a badges group. Concretely, change the footer's inner content to include a badges row before the "Inspect details" span:

```tsx
        {/* Bubble footer info */}
        <div className="flex justify-between items-center px-1 mt-2 text-[10px] font-mono text-splunk-text-muted">
          <span className="uppercase tracking-wider flex items-center gap-2">
            <span className="px-1.5 py-0.5 rounded bg-splunk-bg-card border border-splunk-border">
              SPL match {pct(message.data.spl_confidence)}
            </span>
            <span className="px-1.5 py-0.5 rounded bg-splunk-bg-card border border-splunk-border">
              Decision {pct(decision.confidence_score)}
            </span>
          </span>
          <span className="hover:text-splunk-mint cursor-pointer transition-colors" onClick={onSelect}>
            Inspect details (Click to open sidebar)
          </span>
        </div>
```

- [ ] **Step 3: Add the SPL-match badge to the GENERATED SPL header**

In `ui/src/components/QueryDetails.tsx`, the header (~line 125) renders `<span>GENERATED SPL</span>`. Add a confidence badge next to it. `data` is the component's `QueryResponse` prop. Change:

```tsx
        <div className="flex items-center gap-2 text-xs font-mono text-splunk-text-muted">
          <Terminal size={14} className="text-splunk-mint" />
          <span>GENERATED SPL</span>
        </div>
```

to:

```tsx
        <div className="flex items-center gap-2 text-xs font-mono text-splunk-text-muted">
          <Terminal size={14} className="text-splunk-mint" />
          <span>GENERATED SPL</span>
          <span className="px-1.5 py-0.5 rounded border border-splunk-border text-splunk-mint">
            SPL match {data.spl_confidence === null || data.spl_confidence === undefined
              ? 'n/a'
              : `${Math.round(data.spl_confidence * 100)}%`}
          </span>
        </div>
```

- [ ] **Step 4: Typecheck + build**

Run (from `ui/`): `npm run build`
Expected: `tsc -b` passes with no type errors; `vite build` completes. (If `tsc` flags `spl_confidence` as required-but-missing anywhere a `QueryResponse` literal is constructed in the UI, set it to `null` there.)

- [ ] **Step 5: Commit**

```bash
git add ui/src/api/client.ts ui/src/components/MessageBubble.tsx ui/src/components/QueryDetails.tsx
git commit -m "feat(ui): show SPL-match and Decision confidence badges per query"
```

---

## Notes / follow-ups (not in scope)

- Optional: sync the dead `_RESOURCE_HEAVY_PATTERNS` constant in `engine/app/pipeline/guardrails.py` (~lines 37-46) with the new YAML patterns so it stops misleading readers. Left out to keep changes surgical (the constant is unused — YAML wins). Mention to the reviewer.
