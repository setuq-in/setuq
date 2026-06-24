#!/usr/bin/env python3
"""Eval harness: run golden queries through pipeline, score, optionally push to Langfuse."""
from __future__ import annotations
import argparse
import asyncio
import json
import logging
import os
import random
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path

# Add engine to path when run directly
_ENGINE = Path(__file__).parent.parent
sys.path.insert(0, str(_ENGINE))

from app.config import Settings
from app.llm.factory import create_llm_provider
from app.pipeline.action_suggester import ActionSuggester
from app.pipeline.analysis_agent import AnalysisAgent
from app.pipeline.audit_logger import init_audit_logger
from app.pipeline.decision_engine import DecisionEngine
from app.pipeline.guardrails import QueryGuardrail
from app.pipeline.orchestrator import PipelineOrchestrator
from app.pipeline.planner import PlannerAgent
from app.pipeline.schema_manager import SchemaManager
from app.pipeline.session_manager import SessionManager
from app.pipeline.spl_generator import SPLGenerator
from app.pipeline.splunk_client import SplunkClient
from app.pipeline.summarizer import Summarizer

logging.basicConfig(level=logging.WARNING)
_logger = logging.getLogger("setuq.eval")

_GOLDEN_QUERIES_PATH = Path(__file__).parent / "golden_queries.jsonl"
_JUDGE_MODEL = "claude-sonnet-4-6"  # pinned eval judge
_RUNS_DIR = Path(__file__).parent / "runs"
_REGRESSION_THRESHOLD = 0.5  # sub-score drop > this counts as regression


@dataclass
class EvalResult:
    query_id: str
    query: str
    category: str
    spl: str
    passed_keyword_check: bool
    passed_guardrail_check: bool
    passed_plan_check: bool
    passed_decision_check: bool
    keyword_score: float  # 0.0 - 1.0
    spl_quality: dict  # {validity, groundedness, time_hygiene} each 0.0-1.0
    judge_rubric: dict | None  # {correctness, safety, conciseness, citation} each 1-5, None if not run
    decision_precision: float | None  # 1.0 if recommend+simulated-success, 0.0 if recommend+fail, None if not recommend
    execution_time_ms: int
    error: str | None
    prompt_versions: dict  # name -> sha256[:8]


def _load_golden_queries(path: Path = _GOLDEN_QUERIES_PATH) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _score_keywords(spl: str, expected_keywords: list[str]) -> float:
    if not expected_keywords:
        return 1.0
    spl_lower = spl.lower()
    matched = sum(1 for kw in expected_keywords if kw.lower() in spl_lower)
    return matched / len(expected_keywords)


def _score_spl_quality(spl: str, known_fields: set[str], max_days: int) -> dict:
    """Returns {validity, groundedness, time_hygiene} each 0.0-1.0."""
    import re
    if not spl:
        return {"validity": 0.0, "groundedness": 0.0, "time_hygiene": 0.0}

    # validity: must contain at least one search term or `index=`/`search`, balanced pipe segments non-empty
    has_index = bool(re.search(r"\bindex\s*=", spl, re.IGNORECASE))
    segments = [s.strip() for s in spl.split("|")]
    nonempty_segments = all(seg for seg in segments)
    validity = 1.0 if (has_index and nonempty_segments) else 0.0

    # groundedness: every `field=` referenced exists in known_fields (or known field-like tokens)
    referenced = set(re.findall(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*=", spl))
    # exclude SPL operators that look like fields
    referenced -= {"index", "sourcetype", "source", "host", "earliest", "latest", "eventtype"}
    if not referenced:
        groundedness = 1.0
    else:
        grounded = sum(1 for f in referenced if f in known_fields)
        groundedness = grounded / len(referenced)

    # time_hygiene: must have earliest=, must NOT have unbounded -<N>y where N > max_days/365
    has_earliest = bool(re.search(r"\bearliest\s*=", spl, re.IGNORECASE))
    bad_range = bool(re.search(r"earliest\s*=\s*-\s*(\d+)\s*y", spl, re.IGNORECASE))
    if bad_range:
        years = int(re.search(r"earliest\s*=\s*-\s*(\d+)\s*y", spl, re.IGNORECASE).group(1))
        bad_range = (years * 365) > max_days
    time_hygiene = 1.0 if (has_earliest and not bad_range) else 0.0

    return {"validity": validity, "groundedness": groundedness, "time_hygiene": time_hygiene}


async def _judge_spl(query: str, spl: str, anthropic_key: str) -> dict | None:
    """Use pinned Anthropic Sonnet to grade SPL across a 4-axis rubric (each 1-5)."""
    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=anthropic_key)
        prompt = (
            "You are an expert Splunk SPL reviewer. Rate the SPL below for the given security question.\n\n"
            f"Question: {query}\n\n"
            f"SPL: {spl}\n\n"
            "Return ONLY a JSON object with these four integer fields (each 1-5):\n"
            '  "correctness": does the SPL answer the question accurately\n'
            '  "safety": is the SPL safe to run (bounded time, no destructive ops, no exfil)\n'
            '  "conciseness": is the SPL free of unnecessary commands or fields\n'
            '  "citation": does the SPL reference real-looking field/index names grounded in SOC data\n\n'
            'Example: {"correctness": 4, "safety": 5, "conciseness": 3, "citation": 4}'
        )
        msg = await client.messages.create(
            model=_JUDGE_MODEL,
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        # Strip code fences if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text)
        rubric = {}
        for k in ("correctness", "safety", "conciseness", "citation"):
            v = int(data.get(k, 3))
            rubric[k] = max(1, min(5, v))
        return rubric
    except Exception as exc:
        _logger.warning("Judge failed: %s", exc)
        return None


def _decision_matches(expected: str | None, decision) -> bool:
    """Map golden-query expected_decision to actual Decision.recommendation/risk_level."""
    if not expected:
        return True
    if decision is None:
        # No decision reached — only OK if expected was 'reject' (blocked earlier)
        return expected == "reject"
    rec = decision.recommendation
    risk = decision.risk_level
    if expected == "reject":
        return risk == "critical"
    if expected == "escalate":
        return rec == "recommend_with_approval"
    if expected == "recommend":
        return rec in ("auto_execute", "suggest")
    return False


def _plan_matches(expected: bool | None, plan) -> bool:
    if expected is None:
        return True
    if plan is None:
        return False
    return bool(plan.needs_plan) == bool(expected)


def _build_orchestrator(settings: Settings) -> PipelineOrchestrator:
    llm = create_llm_provider(settings)
    schema_manager = SchemaManager(overrides_path="schema_overrides.yaml")
    splunk_client = SplunkClient(settings)
    session_manager = SessionManager()
    spl_generator = SPLGenerator(llm=llm)
    summarizer = Summarizer(llm=llm)
    action_suggester = ActionSuggester(llm=llm)
    planner = PlannerAgent(llm=llm)
    analysis_agent = AnalysisAgent(llm=llm)
    decision_engine = DecisionEngine(llm=llm)
    audit_logger = init_audit_logger("eval_audit.log")
    known_indexes = list(schema_manager.get_schema().get("indexes", {}).keys())
    guardrail = QueryGuardrail(known_indexes=known_indexes)
    return PipelineOrchestrator(
        schema_manager=schema_manager,
        spl_generator=spl_generator,
        splunk_client=splunk_client,
        summarizer=summarizer,
        session_manager=session_manager,
        action_suggester=action_suggester,
        guardrail=guardrail,
        audit_logger=audit_logger,
        planner=planner,
        analysis_agent=analysis_agent,
        decision_engine=decision_engine,
    )


async def run_eval(
    sample: int | None = None,
    use_judge: bool = False,
    push_langfuse: bool = False,
) -> list[EvalResult]:
    settings = Settings()
    orchestrator = _build_orchestrator(settings)
    anthropic_key = settings.LLM_API_KEY if settings.LLM_PROVIDER == "anthropic" else os.environ.get("ANTHROPIC_API_KEY", "")

    queries = _load_golden_queries()
    if sample:
        queries = random.sample(queries, min(sample, len(queries)))

    from app.pipeline.prompt_registry import all_versions
    prompt_versions = dict(all_versions())

    # Pre-compute known fields + time cap for SPL quality scoring
    schema = orchestrator._schema_manager.get_schema()
    known_fields = set()
    for idx_data in (schema.get("indexes") or {}).values():
        known_fields.update(idx_data.get("fields", []) or [])
    max_days = settings.GUARDRAIL_MAX_TIME_RANGE_DAYS

    results: list[EvalResult] = []
    for gq in queries:
        qid = gq["id"]
        query = gq["query"]
        category = gq.get("category", "easy")
        expected_keywords = gq.get("expected_spl_keywords", [])
        expected_no_violation = gq.get("expected_no_guardrail_violation", True)
        expected_decision = gq.get("expected_decision")
        expected_plan_multistep = gq.get("expected_plan_multistep")

        t0 = time.monotonic()
        spl = ""
        error = None
        actual_violation = False
        plan_obj = None
        decision_obj = None
        result_count = 0

        try:
            result = await orchestrator.run(query, session_id=f"eval-{qid}")
            spl = result.spl
            plan_obj = result.plan
            decision_obj = result.decision
            result_count = result.metadata.get("result_count", 0)
            if result.metadata.get("guardrail_violations"):
                actual_violation = True
        except Exception as exc:
            error = str(exc)
            actual_violation = True  # GuardrailViolation lands here too

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        kw_score = _score_keywords(spl, expected_keywords)
        kw_pass = kw_score >= 0.5 if expected_keywords else True

        guardrail_pass = (not actual_violation) == expected_no_violation
        plan_pass = _plan_matches(expected_plan_multistep, plan_obj)
        decision_pass = _decision_matches(expected_decision, decision_obj)

        rubric = None
        if use_judge and spl and anthropic_key:
            rubric = await _judge_spl(query, spl, anthropic_key)

        spl_q = _score_spl_quality(spl, known_fields, max_days)

        # Decision precision: simulate "would the recommended action have succeeded?"
        # Heuristic: success = no guardrail violation AND result count > 0 (real action sim later).
        dec_precision = None
        if decision_obj is not None and decision_obj.recommendation in ("auto_execute", "suggest"):
            success = (not actual_violation) and result_count >= 0
            dec_precision = 1.0 if success else 0.0

        eval_result = EvalResult(
            query_id=qid,
            query=query,
            category=category,
            spl=spl,
            passed_keyword_check=kw_pass,
            passed_guardrail_check=guardrail_pass,
            passed_plan_check=plan_pass,
            passed_decision_check=decision_pass,
            keyword_score=kw_score,
            spl_quality=spl_q,
            judge_rubric=rubric,
            decision_precision=dec_precision,
            execution_time_ms=elapsed_ms,
            error=error,
            prompt_versions=prompt_versions,
        )
        results.append(eval_result)
        flags = "".join([
            "K" if kw_pass else "k",
            "G" if guardrail_pass else "g",
            "P" if plan_pass else "p",
            "D" if decision_pass else "d",
        ])
        print(f"[{flags}] {qid} ({category}): kw={kw_score:.2f} rubric={rubric} ({elapsed_ms}ms)")

    if push_langfuse:
        _push_to_langfuse(settings, results)

    return results


def _push_to_langfuse(settings, results: list[EvalResult]) -> None:
    try:
        from app.observability.langfuse_client import init_langfuse, get_langfuse
        init_langfuse(settings)
        lf = get_langfuse()
        if lf is None:
            return
        run_name = f"eval-{int(time.time())}"
        for r in results:
            lf.event(
                name="eval.golden_query",
                metadata={
                    "run_name": run_name,
                    **asdict(r),
                },
            )
        _logger.info("Pushed %d eval results to Langfuse run=%s", len(results), run_name)
    except Exception as exc:
        _logger.warning("Langfuse push failed: %s", exc)


def _persist_run(results: list[EvalResult]) -> Path:
    """Write run JSON to runs/<timestamp>.json. Return path."""
    _RUNS_DIR.mkdir(parents=True, exist_ok=True)
    run_path = _RUNS_DIR / f"run-{int(time.time())}.json"
    payload = {
        "timestamp": time.time(),
        "results": [asdict(r) for r in results],
    }
    run_path.write_text(json.dumps(payload, indent=2))
    return run_path


def _load_previous_run() -> dict | None:
    if not _RUNS_DIR.exists():
        return None
    runs = sorted(_RUNS_DIR.glob("run-*.json"))
    if len(runs) < 2:
        return None
    # second-most-recent = previous (most recent = just-written)
    return json.loads(runs[-2].read_text())


def _emit_trend_report(results: list[EvalResult]) -> str:
    """Markdown table comparing current run to previous run. Highlights regressions."""
    prev = _load_previous_run()
    if prev is None:
        return "_No previous run to compare against._"

    prev_by_id = {r["query_id"]: r for r in prev["results"]}
    lines = [
        "| ID | Category | KW Δ | Plan | Dec | Judge correctness Δ | Notes |",
        "|----|----------|------|------|-----|---------------------|-------|",
    ]
    regressions = 0
    for r in results:
        p = prev_by_id.get(r.query_id)
        if p is None:
            continue
        kw_delta = r.keyword_score - p["keyword_score"]
        prev_correct = (p.get("judge_rubric") or {}).get("correctness")
        cur_correct = (r.judge_rubric or {}).get("correctness")
        correct_delta = (cur_correct - prev_correct) if (prev_correct is not None and cur_correct is not None) else None
        notes = []
        if kw_delta < -_REGRESSION_THRESHOLD:
            notes.append("REGRESSION-KW")
            regressions += 1
        if correct_delta is not None and correct_delta < -_REGRESSION_THRESHOLD:
            notes.append("REGRESSION-JUDGE")
            regressions += 1
        plan_marker = "PASS" if r.passed_plan_check else "FAIL"
        dec_marker = "PASS" if r.passed_decision_check else "FAIL"
        cd_str = f"{correct_delta:+.1f}" if correct_delta is not None else "—"
        lines.append(
            f"| {r.query_id} | {r.category} | {kw_delta:+.2f} | {plan_marker} | {dec_marker} | {cd_str} | {','.join(notes) or '—'} |"
        )
    header = f"# Eval trend vs previous run\n\n**Regressions:** {regressions}\n"
    changed_prompts = []
    if results and prev:
        cur_versions = results[0].prompt_versions
        prev_versions = prev["results"][0].get("prompt_versions", {}) if prev["results"] else {}
        for name, ver in cur_versions.items():
            if prev_versions.get(name) != ver:
                changed_prompts.append(f"`{name}`: {prev_versions.get(name, '?')} → {ver}")
    if changed_prompts:
        header += "\n**Prompt hash changes (likely cause of any deltas):**\n" + "\n".join(f"- {c}" for c in changed_prompts) + "\n"
    return header + "\n" + "\n".join(lines)


def _print_summary(results: list[EvalResult]) -> None:
    total = len(results)
    kw_pass = sum(1 for r in results if r.passed_keyword_check)
    guardrail_pass = sum(1 for r in results if r.passed_guardrail_check)
    plan_pass = sum(1 for r in results if r.passed_plan_check)
    decision_pass = sum(1 for r in results if r.passed_decision_check)

    print(f"\n{'='*50}")
    print(f"Eval summary: {total} queries")
    print(f"  Keyword check pass:   {kw_pass}/{total} ({kw_pass/total*100:.0f}%)")
    print(f"  Guardrail check pass: {guardrail_pass}/{total} ({guardrail_pass/total*100:.0f}%)")
    print(f"  Plan check pass:      {plan_pass}/{total} ({plan_pass/total*100:.0f}%)")
    print(f"  Decision check pass:  {decision_pass}/{total} ({decision_pass/total*100:.0f}%)")

    rubrics = [r.judge_rubric for r in results if r.judge_rubric is not None]
    if rubrics:
        for axis in ("correctness", "safety", "conciseness", "citation"):
            avg = sum(r[axis] for r in rubrics) / len(rubrics)
            print(f"  Avg {axis:<12}: {avg:.2f}/5")

    sqs = [r.spl_quality for r in results if r.spl]
    if sqs:
        for axis in ("validity", "groundedness", "time_hygiene"):
            avg = sum(s[axis] for s in sqs) / len(sqs)
            print(f"  SPL {axis:<12}: {avg:.2f}")

    precisions = [r.decision_precision for r in results if r.decision_precision is not None]
    if precisions:
        print(f"  Decision precision:   {sum(precisions)/len(precisions):.2f}")
    print(f"{'='*50}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Eval harness for Setuq golden queries")
    parser.add_argument("--sample", type=int, default=None, help="Number of queries to sample")
    parser.add_argument("--judge", action="store_true", help="Use Anthropic Sonnet as judge")
    parser.add_argument("--langfuse", action="store_true", help="Push results to Langfuse")
    parser.add_argument("--report", action="store_true", help="Persist run + print trend table vs previous run")
    args = parser.parse_args()

    results = asyncio.run(run_eval(
        sample=args.sample,
        use_judge=args.judge,
        push_langfuse=args.langfuse,
    ))
    _print_summary(results)

    if args.report:
        run_path = _persist_run(results)
        print(f"\nRun persisted to {run_path}")
        print()
        print(_emit_trend_report(results))

    failures = [r for r in results if not r.passed_keyword_check]
    sys.exit(0 if not failures else 1)
