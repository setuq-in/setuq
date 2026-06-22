import uuid

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    query: str
    session_id: str | None = None


class QueryMetadata(BaseModel):
    result_count: int
    execution_time_ms: int


class ActionSuggestionSchema(BaseModel):
    action: str
    target: str
    reasoning: str
    risk_level: str


class InvestigationStepSchema(BaseModel):
    description: str
    spl_hint: str | None


class PlanSchema(BaseModel):
    needs_plan: bool
    steps: list[InvestigationStepSchema]
    reasoning: str


class AnomalySchema(BaseModel):
    description: str
    severity: str
    evidence: str


class PatternSchema(BaseModel):
    description: str
    confidence: float
    affected_entities: list[str]


class AnalysisSchema(BaseModel):
    anomalies: list[AnomalySchema]
    patterns: list[PatternSchema]
    summary: str


class DecisionSchema(BaseModel):
    confidence_score: float
    risk_level: str
    reasoning: str
    recommendation: str
    priority_actions: list[str]


class ChartSpec(BaseModel):
    chart_type: str
    x_field: str | None
    y_fields: list[str]
    series_field: str | None
    title: str
    confidence: float
    truncated: bool = False
    truncation_note: str | None = None


class ChartOverrideRequest(BaseModel):
    chart_type: str


class QueryResponse(BaseModel):
    query: str
    spl: str
    spl_explanation: str
    results: list[dict]
    summary: str
    plan: PlanSchema
    analysis: AnalysisSchema
    decision: DecisionSchema
    actions: list[ActionSuggestionSchema]
    metadata: QueryMetadata
    session_id: str
    chart_spec: ChartSpec | None = None
    message_id: str = Field(default_factory=lambda: uuid.uuid4().hex)


class ErrorResponse(BaseModel):
    error: str
    message: str
    spl: str | None = None


class HealthResponse(BaseModel):
    status: str
