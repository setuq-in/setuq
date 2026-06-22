import type { QueryResponse, ActionSuggestion } from '../api/client';
import { QueryDetails } from './QueryDetails';
import { AlertCircle, Brain, User, CheckCircle2, ChevronRight, Activity, TrendingUp } from 'lucide-react';

export interface UserMessage {
  type: 'user';
  text: string;
}

export interface SystemMessage {
  type: 'system';
  data: QueryResponse;
  steps?: string[]; // Live progress steps
}

export interface ErrorMessage {
  type: 'error';
  text: string;
  spl?: string;
}

export type Message = UserMessage | SystemMessage | ErrorMessage;

interface MessageBubbleProps {
  message: Message;
  isSelected?: boolean;
  onSelect?: () => void;
  onSelectAction?: (action: ActionSuggestion) => void;
}

// Heuristically extract single KPI if results are simple
function extractKPI(results: Record<string, string>[]) {
  if (!results || results.length === 0) return null;
  if (results.length > 5) return null; // Only for aggregate single values

  const firstRow = results[0];
  // Find fields that represent metrics
  const metricKeys = ['count', 'sum', 'avg', 'percentage', 'pct', 'total', 'error_rate', 'failures', 'threat_score', 'severity'];
  
  for (const key of metricKeys) {
    for (const actualKey of Object.keys(firstRow)) {
      if (actualKey.toLowerCase().includes(key)) {
        return {
          label: actualKey.replace(/_/g, ' ').toUpperCase(),
          value: firstRow[actualKey]
        };
      }
    }
  }

  // Fallback: if there is exactly one row and 1-2 columns, grab the first numeric value
  if (results.length === 1) {
    const keys = Object.keys(firstRow);
    for (const key of keys) {
      const val = firstRow[key];
      if (val !== undefined && !isNaN(Number(val)) && key.toLowerCase() !== 'time') {
        return {
          label: key.replace(/_/g, ' ').toUpperCase(),
          value: val
        };
      }
    }
  }

  return null;
}

export function MessageBubble({ message, isSelected, onSelect, onSelectAction }: MessageBubbleProps) {
  if (message.type === 'user') {
    return (
      <div className="flex justify-end gap-3.5 max-w-[85%] ml-auto mb-6">
        <div className="flex flex-col items-end">
          <div className="px-4 py-3 bg-splunk-bg-card border border-splunk-border rounded-2xl rounded-tr-none text-splunk-text-main shadow-md">
            <p className="text-sm font-sans select-text">{message.text}</p>
          </div>
          <span className="text-[10px] font-mono text-splunk-text-muted mt-1.5 flex items-center gap-1">
            <User size={10} />
            <span>ANALYST</span>
          </span>
        </div>
      </div>
    );
  }

  if (message.type === 'error') {
    return (
      <div className="flex gap-3.5 max-w-[90%] mr-auto mb-6">
        <div className="flex-shrink-0 w-8 h-8 rounded-full border border-splunk-red/40 bg-splunk-red/15 flex items-center justify-center text-splunk-red">
          <AlertCircle size={16} />
        </div>
        <div className="flex flex-col">
          <div className="px-4 py-3 bg-[#1e1315] border border-splunk-red/45 rounded-2xl rounded-tl-none shadow-md">
            <div className="flex items-center gap-2 text-splunk-red text-xs font-semibold mb-1 font-sans">
              <span>QUERY EXECUTION ERROR</span>
            </div>
            <p className="text-xs font-mono text-splunk-red leading-normal select-text">
              {message.text}
            </p>
            {message.spl && (
              <pre className="mt-2.5 p-2 bg-black/35 rounded border border-splunk-red/20 font-mono text-[11px] text-splunk-text-muted overflow-x-auto">
                {message.spl}
              </pre>
            )}
          </div>
          <span className="text-[10px] font-mono text-splunk-text-muted mt-1.5 uppercase">
            SYSTEM ALARM
          </span>
        </div>
      </div>
    );
  }

  // System Message Card layout
  const kpi = extractKPI(message.data.results);
  const decision = message.data.decision;

  // Decide indicator color based on severity/risk
  const getDecisionBorder = (recommendation: string) => {
    switch (recommendation.toLowerCase()) {
      case 'auto_execute':
        return 'border-l-4 border-l-splunk-mint';
      case 'recommend':
        return 'border-l-4 border-l-splunk-blue';
      case 'escalate':
        return 'border-l-4 border-l-splunk-red';
      default:
        return 'border-l-4 border-l-splunk-orange';
    }
  };

  return (
    <div 
      onClick={onSelect}
      className={`flex gap-3.5 max-w-[92%] mr-auto mb-6 transition-all group cursor-pointer ${
        isSelected ? 'scale-[1.005]' : ''
      }`}
    >
      {/* Avatar */}
      <div className={`flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center border transition-all ${
        isSelected 
          ? 'bg-splunk-mint/20 border-splunk-mint text-splunk-mint shadow-[0_0_8px_rgba(79,164,132,0.4)]' 
          : 'bg-splunk-bg-sidebar border-splunk-border text-splunk-text-muted group-hover:border-splunk-mint group-hover:text-splunk-mint'
      }`}>
        <Brain size={16} />
      </div>

      <div className="flex-1 min-w-0">
        {/* Main Response Box */}
        <div className={`bg-splunk-bg-sidebar border rounded-2xl rounded-tl-none shadow-md overflow-hidden transition-all ${
          isSelected 
            ? 'border-splunk-mint/80 bg-splunk-bg-sidebar/95 shadow-[0_0_12px_rgba(79,164,132,0.15)]' 
            : 'border-splunk-border group-hover:border-splunk-border/80'
        } ${getDecisionBorder(decision.recommendation)}`}>
          {/* Summary / Narration */}
          <div className="p-4 border-b border-splunk-border">
            <p className="text-sm text-splunk-text-main font-sans leading-relaxed select-text">
              {message.data.summary}
            </p>
          </div>

          {/* KPI Dashboard Panel (Splunk Studio Styled) */}
          {kpi && (
            <div className="px-4 py-3 bg-[#15171b] border-b border-splunk-border flex items-center gap-6">
              <div className="flex items-center gap-2 border-r border-splunk-border/60 pr-6">
                <TrendingUp size={16} className="text-splunk-mint" />
                <span className="text-[10px] font-semibold text-splunk-text-muted font-sans uppercase tracking-wider">
                  Key Metric
                </span>
              </div>
              <div className="flex items-baseline gap-2">
                <span className="text-2xl font-bold font-mono text-splunk-text-main tracking-tight">
                  {kpi.value}
                </span>
                <span className="text-xs text-splunk-text-muted font-sans uppercase font-medium">
                  {kpi.label}
                </span>
              </div>
            </div>
          )}

          {/* Stepper Pipeline Execution Status */}
          <div className="px-4 py-2.5 bg-[#121316] border-b border-splunk-border flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Activity size={12} className="text-splunk-blue animate-pulse" />
              <span className="text-[10px] font-mono text-splunk-text-muted uppercase">
                Pipeline execution logs
              </span>
            </div>
            <div className="flex items-center gap-1 text-[10px] font-mono text-splunk-mint">
              <CheckCircle2 size={11} />
              <span>pipeline.completed</span>
            </div>
          </div>

          {/* SPL & Table details */}
          <div className="p-4 bg-splunk-bg-main/60">
            <QueryDetails data={message.data} onSelectAction={onSelectAction} />
          </div>
        </div>

        {/* Bubble footer info */}
        <div className="flex justify-between items-center px-1 mt-2 text-[10px] font-mono text-splunk-text-muted">
          <span className="uppercase tracking-wider flex items-center gap-1">
            <span>SETUQ AGENT</span>
            <ChevronRight size={10} />
            <span className="text-splunk-mint">SUCCESS</span>
          </span>
          <span className="hover:text-splunk-mint cursor-pointer transition-colors" onClick={onSelect}>
            Inspect details (Click to open sidebar)
          </span>
        </div>
      </div>
    </div>
  );
}
