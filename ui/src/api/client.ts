export interface QueryMetadata {
  result_count: number;
  execution_time_ms: number;
}

export interface ActionSuggestion {
  action: string;
  target: string;
  reasoning: string;
  risk_level: string;
}

export interface InvestigationStep {
  description: string;
  spl_hint: string | null;
}

export interface PlanData {
  needs_plan: boolean;
  steps: InvestigationStep[];
  reasoning: string;
}

export interface AnomalyData {
  description: string;
  severity: string;
  evidence: string;
}

export interface PatternData {
  description: string;
  confidence: number;
  affected_entities: string[];
}

export interface AnalysisData {
  anomalies: AnomalyData[];
  patterns: PatternData[];
  summary: string;
}

export interface DecisionData {
  confidence_score: number;
  risk_level: string;
  reasoning: string;
  recommendation: string;
  priority_actions: string[];
}

export interface QueryResponse {
  query: string;
  spl: string;
  spl_explanation: string;
  results: Record<string, string>[];
  summary: string;
  plan: PlanData;
  analysis: AnalysisData;
  decision: DecisionData;
  actions: ActionSuggestion[];
  metadata: QueryMetadata;
  session_id: string;
}

export interface ErrorResponse {
  detail: string;
}

export interface SchemaField {
  name: string;
  type?: string;
  description?: string;
}

export interface SchemaSourcetype {
  description?: string;
  time_field?: string;
  fields?: SchemaField[];
  relationships?: { field: string; references: string; purpose: string }[];
  derived_metrics?: { name: string; formula: string; meaning: string }[];
  common_questions?: string[];
}

export interface SchemaIndex {
  description?: string;
  role?: string;
  sourcetypes: Record<string, SchemaSourcetype>;
}

export interface SchemaGlossaryItem {
  term: string;
  maps_to: string;
}

export interface SchemaPatternItem {
  name: string;
  applies_to: string;
  steps: string[];
}

export interface SchemaResponse {
  indexes: Record<string, SchemaIndex>;
  glossary?: SchemaGlossaryItem[];
  investigation_patterns?: SchemaPatternItem[];
}

export async function sendQuery(query: string, sessionId?: string | null): Promise<QueryResponse> {
  const body: Record<string, string> = { query };
  if (sessionId) body.session_id = sessionId;

  const response = await fetch('/api/query', {
    method: 'POST',
    headers: { 
      'Content-Type': 'application/json',
      'X-Allow-Auto-Execute': 'true' // Opt-in to auto-execute decisions
    },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const error: ErrorResponse = await response.json().catch(() => ({ detail: 'Query failed' }));
    throw new Error(error.detail || 'Query failed');
  }

  return response.json();
}

export async function getSchema(): Promise<SchemaResponse> {
  const response = await fetch('/api/schema');
  if (!response.ok) {
    throw new Error('Failed to fetch Splunk schema');
  }
  return response.json();
}

export async function refreshSchema(): Promise<SchemaResponse> {
  const response = await fetch('/api/schema/refresh', { method: 'POST' });
  if (!response.ok) {
    throw new Error('Failed to refresh Splunk schema');
  }
  const data = await response.json();
  return data.schema;
}

export async function checkHealth(): Promise<boolean> {
  try {
    const response = await fetch('/api/health');
    return response.ok;
  } catch {
    return false;
  }
}
