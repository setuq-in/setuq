import json
from dataclasses import dataclass, field
from pydantic import BaseModel, Field
from app.llm.base import LLMProvider
from app.pipeline import prompt_registry
from app.pipeline.llm_utils import generate_validated, LLMOutputValidationError
from app.pipeline.result_sampling import sample_for_llm


class _AnomalySchema(BaseModel):
    description: str = ""
    severity: str = "medium"
    evidence: str = ""


class _PatternSchema(BaseModel):
    description: str = ""
    confidence: float = 0.0
    affected_entities: list[str] = Field(default_factory=list)


class _AnalysisSchema(BaseModel):
    anomalies: list[_AnomalySchema] = Field(default_factory=list)
    patterns: list[_PatternSchema] = Field(default_factory=list)
    summary: str = ""


@dataclass
class Anomaly:
    description: str
    severity: str
    evidence: str


@dataclass
class Pattern:
    description: str
    confidence: float
    affected_entities: list[str]


@dataclass
class AnalysisResult:
    anomalies: list[Anomaly]
    patterns: list[Pattern]
    summary: str


class AnalysisAgent:
    def __init__(self, llm: LLMProvider):
        self._llm = llm

    async def analyze(
        self,
        query: str,
        spl: str,
        results: list[dict],
    ) -> AnalysisResult:
        """Perform deep analysis on query results."""
        sample, sketch = sample_for_llm(results, k=60) if results else ([], {})
        results_str = json.dumps(sample, indent=2) if sample else "No results."
        if sketch.get("total_rows", 0) > len(sample):
            results_str += f"\n\n(Showing {sketch['sampled']} of {sketch['total_rows']} total rows. Fields seen: {', '.join(sketch['fields'])})"

        user_prompt = f"""Original question: {query}

SPL query: {spl}

Results:
{results_str}"""

        try:
            data = await generate_validated(
                llm=self._llm,
                system_prompt=prompt_registry.get("analysis_agent"),
                history=[],
                user_prompt=user_prompt,
                model_class=_AnalysisSchema,
            )
        except LLMOutputValidationError:
            return AnalysisResult(anomalies=[], patterns=[], summary="Invalid analysis — could not be parsed.")

        return AnalysisResult(
            anomalies=[Anomaly(description=a.description, severity=a.severity, evidence=a.evidence) for a in data.anomalies],
            patterns=[Pattern(description=p.description, confidence=p.confidence, affected_entities=p.affected_entities) for p in data.patterns],
            summary=data.summary,
        )
