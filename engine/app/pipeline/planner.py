from dataclasses import dataclass
from typing import Optional
from pydantic import BaseModel, model_validator
from app.llm.base import LLMProvider
from app.pipeline import prompt_registry
from app.pipeline.llm_utils import generate_validated, LLMOutputValidationError


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
                system_prompt=prompt_registry.get("planner"),
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
