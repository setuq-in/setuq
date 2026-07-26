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

_DOCS_BASELINE_PATH = Path(__file__).parents[2] / "docs" / "benchmark-baseline.md"
_PROMPTS_PATH = Path(__file__).parent.parent / "config" / "prompts.yaml"


async def run_provider_cell(provider: str, session_backend: str) -> dict:
    if session_backend == "redis":
        return {
            "provider": provider,
            "backend": session_backend,
            "skipped": True,
            "error": "redis backend not wired in bench harness (build_bench_orchestrator uses in-memory SessionManager)",
        }

    settings_kwargs: dict = {"LLM_PROVIDER": provider}
    settings = Settings(**settings_kwargs)

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
    first_error: str | None = None
    for gq in queries:
        t0 = time.monotonic()
        try:
            res = await orch.run(gq["query"], session_id=f"pm-{gq['id']}")
            kw_scores.append(_score_keywords(res.spl, gq.get("expected_spl_keywords", [])))
            costs.append(audit.last_cost())
        except Exception as exc:
            kw_scores.append(0.0)
            first_error = first_error or f"{type(exc).__name__}: {exc}"
        e2e.append((time.monotonic() - t0) * 1000.0)

    # A provider that's unreachable/unauthenticated (e.g. no API key, Ollama not
    # serving) constructs fine but fails every query. Reporting that as a "0%"
    # row would look like a real quality regression rather than "not run" -- so
    # treat "every single query errored" as skipped instead of a real result.
    if first_error is not None and all(s == 0.0 for s in kw_scores):
        return {"provider": provider, "backend": session_backend, "skipped": True, "error": first_error}

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

    # Standalone entrypoint: not run under pytest, so the shipped prompts
    # aren't loaded by tests/conftest.py's autouse fixture -- load them here
    # (see bench/latency_bench.py for the same pattern).
    from app.pipeline import prompt_registry
    if not prompt_registry.all_versions():
        prompt_registry.load(str(_PROMPTS_PATH))

    cells = asyncio.run(_run_matrix(args.providers.split(","), args.backends.split(",")))
    lines = ["| Provider | Backend | KW pass | p95 ms | $/query |",
             "|----------|---------|---------|--------|---------|"]
    for c in cells:
        if c.get("skipped"):
            lines.append(f"| {c['provider']} | {c['backend']} | skipped | — | — |")
        else:
            lines.append(f"| {c['provider']} | {c['backend']} | {c['keyword_pass']:.0%} | "
                         f"{c['e2e_p95_ms']:.0f} | {c['cost_usd_mean']:.4f} |")
    skipped = [c for c in cells if c.get("skipped")]
    md = "# Setuq benchmark baseline\n\n## Provider / scale matrix\n\n" + "\n".join(lines) + "\n"
    if skipped:
        md += "\n### Skipped / not-run cells\n\n"
        md += "Cells marked `skipped` above either lack credentials, are unreachable, or errored on every query. Reason per cell (a real error here is NOT a benign 'not configured' -- inspect it):\n\n"
        for c in skipped:
            md += f"- `{c['provider']}` / `{c['backend']}`: {c.get('error', 'unknown')}\n"
    md += "\n### Known limitations\n\n"
    md += "- The `redis` session backend is not yet wired into the bench harness (`build_bench_orchestrator` uses in-memory `SessionManager`); `--backends redis` rows are reported as `skipped`, not measured.\n"
    _DOCS_BASELINE_PATH.write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
