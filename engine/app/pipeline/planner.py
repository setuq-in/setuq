from dataclasses import dataclass
from typing import Optional
from pydantic import BaseModel, model_validator
from app.llm.base import LLMProvider
from app.pipeline.llm_utils import generate_validated, LLMOutputValidationError


SYSTEM_PROMPT = """You are a SOC investigation planner. Given a natural language security query, decide whether it can be answered with a single SPL query or requires a multi-step investigation plan.

Default to needs_plan=false. Use needs_plan=true ONLY when at least one of these is true:
- The query compares two or more time windows (e.g., "this week vs last week").
- The query correlates data from two or more distinct sources/indexes that cannot be joined in one SPL.
- The query requires pivoting on an intermediate result (e.g., find IPs, then for each IP find sessions).
- The query asks to investigate, trace, or build a timeline across multiple entity types.

If a single SPL command can answer the question (basic search, filter, stats, sort, top, dc), return needs_plan=false.

Examples:
- "Show top 10 source IPs with failed logins today" → needs_plan=false (single stats query).
- "Compare login failure rate this week vs last week per user" → needs_plan=true (two windows + pivot).
- "List events from index=main in the last hour" → needs_plan=false (basic search).
- "Investigate the breach: who got in, what they touched, where they came from" → needs_plan=true (multi-entity pivot).

Return a JSON object with:
- "needs_plan": boolean
- "steps": array of {"description": str, "spl_hint": str|null}. For needs_plan=false return a single step.
- "reasoning": brief string

Output ONLY valid JSON, no markdown fences."""


class _StepSchema(BaseModel):
    description: str = ""
    spl_hint: Optional[str] = None


class _PlanSchema(BaseModel):
    needs_plan: bool = False
    steps: list[_StepSchema] = []
    reasoning: str = ""

    @model_validator(mode="before")
    @classmethod
    def _filter_steps(cls, v):
        if isinstance(v, dict) and "steps" in v:
            v = dict(v)
            v["steps"] = [s for s in v["steps"] if isinstance(s, dict)]
        return v


@dataclass
class InvestigationStep:
    description: str
    spl_hint: str | None


@dataclass
class InvestigationPlan:
    needs_plan: bool
    steps: list[InvestigationStep]
    reasoning: str


class PlannerAgent:
    def __init__(self, llm: LLMProvider):
        self._llm = llm

    async def plan(self, query: str, schema_context: str) -> InvestigationPlan:
        """Analyze a query and produce an investigation plan."""
        user_prompt = f"""Query: {query}

{schema_context}"""

        try:
            data = await generate_validated(
                llm=self._llm,
                system_prompt=SYSTEM_PROMPT,
                history=[],
                user_prompt=user_prompt,
                model_class=_PlanSchema,
                max_retries=2,
            )
        except LLMOutputValidationError:
            return InvestigationPlan(
                needs_plan=False,
                steps=[InvestigationStep(description="Execute query directly", spl_hint=None)],
                reasoning="Could not parse plan — falling back to direct execution",
            )

        steps = [
            InvestigationStep(description=s.description, spl_hint=s.spl_hint)
            for s in data.steps
        ] or [InvestigationStep(description="Execute query directly", spl_hint=None)]

        return InvestigationPlan(
            needs_plan=data.needs_plan,
            steps=steps,
            reasoning=data.reasoning,
        )

from app.pipeline.prompt_registry import register as _reg_prompt
_reg_prompt("planner", SYSTEM_PROMPT)
