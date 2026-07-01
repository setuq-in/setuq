import json
from dataclasses import dataclass
from pydantic import BaseModel, Field, model_validator
from app.llm.base import LLMProvider
from app.pipeline import prompt_registry
from app.pipeline.llm_utils import generate_validated, LLMOutputValidationError
from app.pipeline.result_sampling import sample_for_llm


class _ActionItemSchema(BaseModel):
    action: str = "unknown"
    target: str = ""
    reasoning: str = ""
    risk_level: str = "medium"


class _ActionsSchema(BaseModel):
    actions: list[_ActionItemSchema] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, v):
        if isinstance(v, list):
            return {"actions": [i for i in v if isinstance(i, dict)]}
        if isinstance(v, dict) and "actions" not in v:
            return {"actions": []}
        return v


@dataclass
class ActionSuggestion:
    action: str
    target: str
    reasoning: str
    risk_level: str


class ActionSuggester:
    def __init__(self, llm: LLMProvider):
        self._llm = llm

    async def suggest(
        self,
        query: str,
        spl: str,
        results: list[dict],
        summary: str,
    ) -> list[ActionSuggestion]:
        """Analyze query results and suggest security actions."""
        sample, sketch = sample_for_llm(results, k=50) if results else ([], {})
        results_str = json.dumps(sample, indent=2) if sample else "No results."
        if sketch.get("total_rows", 0) > len(sample):
            results_str += f"\n\n(Showing {sketch['sampled']} of {sketch['total_rows']} total rows. Fields seen: {', '.join(sketch['fields'])})"

        user_prompt = f"""Original question: {query}

SPL query: {spl}

Results (sample):
{results_str}

Summary: {summary}"""

        try:
            data = await generate_validated(
                llm=self._llm,
                system_prompt=prompt_registry.get("action_suggester"),
                history=[],
                user_prompt=user_prompt,
                model_class=_ActionsSchema,
                max_retries=2,
            )
        except LLMOutputValidationError:
            return []

        return [
            ActionSuggestion(
                action=a.action,
                target=a.target,
                reasoning=a.reasoning,
                risk_level=a.risk_level,
            )
            for a in data.actions
        ]
