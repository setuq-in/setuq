from __future__ import annotations
import asyncio
from dataclasses import dataclass

from app.llm.base import LLMResponse
from bench.factory import build_bench_orchestrator
from bench.mocks import FaultyMockLLM
from tests.pipeline.test_orchestrator import MockLLM, _mock_usage


@dataclass
class FaultOutcome:
    fault: str
    served: bool
    audited: bool
    error: str | None


class _DestructiveSPLMock(MockLLM):
    """Like MockLLM, but the SPL generator branch returns SPL that trips the
    real guardrail's resource-heavy `| delete` rule (config/guardrails.yaml),
    instead of MockLLM's normal fixed, harmless SPL. Used to make the
    guardrail_block scenario actually exercise QueryGuardrail.validate()
    rather than relying on the natural-language query text (MockLLM.generate
    ignores the query and returns a fixed SPL regardless of input)."""

    async def generate(self, system_prompt: str, history: list[dict], user_prompt: str) -> LLMResponse:
        if "SPL query generator" in system_prompt:
            return LLMResponse(
                content="index=chocolate_index sourcetype=sales earliest=-1d | delete",
                usage=_mock_usage(),
            )
        return await super().generate(system_prompt, history, user_prompt)


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

    # Each scenario gets its own subdirectory so the schema-override/audit files
    # of one orchestrator never collide with another's.
    llm_dir, gb_dir, sd_dir = tmp_path / "llm", tmp_path / "gb", tmp_path / "sd"
    for d in (llm_dir, gb_dir, sd_dir):
        d.mkdir(parents=True, exist_ok=True)

    # 1. Hard LLM failure — expect a clean raise (harness exhausts retries), not a hang.
    orch, audit = build_bench_orchestrator(llm_dir, llm=FaultyMockLLM(mode="error"))
    served, audited, err = await _drive(orch, audit, "Show failed logins")
    outcomes.append(FaultOutcome("llm_error", served, audited, err))

    # 2. Guardrail block — SPL generator returns a `| delete` query, tripping the
    #    real guardrail's resource-heavy pattern ("delete command not allowed").
    #    Query text is irrelevant to MockLLM-derived mocks; what matters is the
    #    SPL the mock returns for the SPL-generator system prompt.
    orch, audit = build_bench_orchestrator(gb_dir, llm=_DestructiveSPLMock())
    served, audited, err = await _drive(orch, audit, "DELETE all logs")
    outcomes.append(FaultOutcome("guardrail_block", served, audited, err))

    # 3. Splunk down — execute_spl raises. Expect a clean raise, no hang.
    def _boom(*a, **k):
        raise RuntimeError("splunk unreachable")
    orch, audit = build_bench_orchestrator(sd_dir, llm=MockLLM())
    orch._splunk_client.execute_spl.side_effect = _boom
    served, audited, err = await _drive(orch, audit, "Show me total revenue by store")
    outcomes.append(FaultOutcome("splunk_down", served, audited, err))

    return outcomes


if __name__ == "__main__":
    import tempfile
    from pathlib import Path
    from app.pipeline import prompt_registry

    # Standalone entrypoint: not run under pytest, so the shipped prompts
    # aren't loaded by tests/conftest.py's autouse fixture — load them here
    # (see bench/latency_bench.py for the same pattern).
    if not prompt_registry.all_versions():
        _prompts_path = Path(__file__).parent.parent / "config" / "prompts.yaml"
        prompt_registry.load(str(_prompts_path))

    res = asyncio.run(run_resilience_bench(Path(tempfile.mkdtemp())))
    for o in res:
        print(f"{o.fault:<16} served={o.served} audited={o.audited} error={o.error}")
