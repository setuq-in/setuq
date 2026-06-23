import json
from dataclasses import dataclass, field
from pydantic import BaseModel, Field
from app.llm.base import LLMProvider
from app.pipeline import prompt_registry
from app.pipeline.llm_utils import generate_validated, LLMOutputValidationError


SYSTEM_PROMPT = """You are a SOC decision engine. Given an investigation's results, analysis, and suggested actions, produce a confidence-scored decision with full reasoning.

Rules:
- Return a JSON object with:
  - "confidence_score": float 0.0-1.0 — overall confidence in the assessment
  - "risk_level": "low"|"medium"|"high"|"critical"
  - "reasoning": string — detailed explanation of how you reached this decision (2-4 sentences)
  - "recommendation": "suggest"|"recommend_with_approval"|"auto_execute"
    - confidence < 0.7 → "suggest"
    - confidence 0.7-0.9 → "recommend_with_approval"
    - confidence > 0.9 AND risk_level is "low" → "auto_execute"
    - Otherwise → "recommend_with_approval"
  - "priority_actions": array of strings — ordered list of most important actions to take
- Base confidence on: data quality, pattern clarity, number of corroborating signals, severity of findings
- Be conservative — when uncertain, lower the confidence score
- Output ONLY valid JSON, no markdown fences"""


class _DecisionSchema(BaseModel):
    confidence_score: float = 0.0
    risk_level: str = "medium"
    reasoning: str = ""
    recommendation: str = "suggest"
    priority_actions: list[str] = Field(default_factory=list)


@dataclass
class Decision:
    confidence_score: float
    risk_level: str
    reasoning: str
    recommendation: str
    priority_actions: list[str]


class DecisionEngine:
    def __init__(self, llm: LLMProvider):
        self._llm = llm

    async def decide(
        self,
        query: str,
        summary: str,
        analysis_summary: str,
        anomaly_count: int,
        pattern_count: int,
        actions_suggested: list[dict],
    ) -> Decision:
        """Produce a confidence-scored decision with reasoning."""
        actions_str = json.dumps(actions_suggested, indent=2) if actions_suggested else "No actions suggested."

        user_prompt = f"""Original question: {query}

Investigation summary: {summary}

Analysis: {analysis_summary}
Anomalies detected: {anomaly_count}
Patterns identified: {pattern_count}

Suggested actions:
{actions_str}"""

        try:
            data = await generate_validated(
                llm=self._llm,
                system_prompt=prompt_registry.resolve("decision_engine", SYSTEM_PROMPT),
                history=[],
                user_prompt=user_prompt,
                model_class=_DecisionSchema,
            )
        except LLMOutputValidationError:
            return Decision(
                confidence_score=0.0,
                risk_level="medium",
                reasoning="Invalid decision format — could not be parsed from LLM response.",
                recommendation="suggest",
                priority_actions=[],
            )

        confidence = data.confidence_score
        risk_level = data.risk_level
        recommendation = data.recommendation

        if recommendation not in ("suggest", "recommend_with_approval", "auto_execute"):
            recommendation = self._compute_recommendation(confidence, risk_level)

        return Decision(
            confidence_score=confidence,
            risk_level=risk_level,
            reasoning=data.reasoning,
            recommendation=recommendation,
            priority_actions=data.priority_actions,
        )

    @staticmethod
    def _compute_recommendation(confidence: float, risk_level: str) -> str:
        """Apply threshold rules from spec: <0.7 suggest, 0.7-0.9 recommend, >0.9+low auto."""
        if confidence < 0.7:
            return "suggest"
        if confidence > 0.9 and risk_level == "low":
            return "auto_execute"
        return "recommend_with_approval"

prompt_registry.register("decision_engine", SYSTEM_PROMPT)
