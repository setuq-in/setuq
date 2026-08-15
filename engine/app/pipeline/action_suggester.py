from dataclasses import dataclass
from pydantic import BaseModel, Field, model_validator
from app.llm.base import LLMProvider
from app.pipeline import prompt_registry
from app.pipeline.llm_utils import generate_validated, LLMOutputValidationError
from app.pipeline.result_sampling import format_results_for_llm


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
        results_str = format_results_for_llm(results, k=50)

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
