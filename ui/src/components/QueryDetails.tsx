import { useState } from 'react';
import { Copy, Terminal, ExternalLink, ShieldAlert, CheckCircle2, AlertTriangle } from 'lucide-react';
import type { QueryResponse, ActionSuggestion } from '../api/client';

interface QueryDetailsProps {
  data: QueryResponse;
  onSelectAction?: (action: ActionSuggestion) => void;
}

export function highlightSPL(spl: string) {
  if (!spl) return '';
  const commands = /\b(search|stats|timechart|chart|eval|where|rex|lookup|dedup|head|tail|sort|table|fields|rename|join|metadata|fieldsummary|inputlookup)\b/gi;
  const functions = /\b(count|sum|avg|min|max|dc|values|list|earliest|latest|now|mvfilter|coalesce|if|like|cidrmatch)\b/gi;
  const argumentsPattern = /\b(by|as|index|sourcetype|source|host)\b/gi;
  const operators = /(!=|&gt;=|&lt;=|&gt;|&lt;|=|\b(AND|OR|NOT)\b|\|)/g;

  // Escape HTML characters
  let escaped = spl
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');

  // Wrap tokens in styling spans
  // Operators must be highlighted first to avoid matching the characters (like <, >, or =) of newly introduced span tags.
  escaped = escaped.replace(operators, '<span class="text-orange-400">$1</span>');
  escaped = escaped.replace(commands, '<span class="text-sky-400 font-semibold">$1</span>');
  escaped = escaped.replace(functions, '<span class="text-pink-400">$1</span>');
  escaped = escaped.replace(argumentsPattern, '<span class="text-emerald-400 font-medium">$1</span>');

  return escaped;
}

export function QueryDetails({ data, onSelectAction }: QueryDetailsProps) {
  const [copied, setCopied] = useState(false);
  const [activeTab, setActiveTab] = useState<'results' | 'explanation' | 'actions'>('results');

  const handleCopy = async () => {
    await navigator.clipboard.writeText(data.spl);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const headers = data.results.length > 0 ? Object.keys(data.results[0]) : [];

  // Semantic color for risk level
  const getRiskColor = (risk: string) => {
    switch (risk.toLowerCase()) {
      case 'high':
      case 'critical':
        return 'text-splunk-red border-splunk-red/30 bg-splunk-red/10';
      case 'medium':
        return 'text-splunk-orange border-splunk-orange/30 bg-splunk-orange/10';
      default:
        return 'text-splunk-mint border-splunk-mint/30 bg-splunk-mint/10';
    }
  };

  return (
    <div className="mt-4 border border-splunk-border rounded-lg bg-splunk-bg-sidebar overflow-hidden shadow-lg">
      {/* SPL Block Header */}
      <div className="flex items-center justify-between px-4 py-3 bg-splunk-bg-sidebar border-b border-splunk-border">
        <div className="flex items-center gap-2 text-xs font-mono text-splunk-text-muted">
          <Terminal size={14} className="text-splunk-mint" />
          <span>GENERATED SPL</span>
        </div>
        <div className="flex gap-2">
          <button
            onClick={handleCopy}
            className="flex items-center gap-1.5 px-2.5 py-1 text-xs font-sans font-medium rounded border border-splunk-border bg-splunk-bg-card hover:bg-splunk-bg-hover text-splunk-text-main transition-colors cursor-pointer"
          >
            <Copy size={12} />
            <span>{copied ? 'Copied' : 'Copy'}</span>
          </button>
          <a
            href={`https://splunk-searchhead.internal/app/search/search?q=${encodeURIComponent(data.spl)}`}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1.5 px-2.5 py-1 text-xs font-sans font-medium rounded bg-splunk-blue hover:bg-sky-700 text-white transition-colors cursor-pointer"
          >
            <ExternalLink size={12} />
            <span>Run in Search</span>
          </a>
        </div>
      </div>

      {/* Code Area */}
      <div className="p-4 bg-[#08090b] overflow-x-auto border-b border-splunk-border">
        <pre 
          className="font-mono text-sm leading-relaxed whitespace-pre-wrap select-all"
          dangerouslySetInnerHTML={{ __html: highlightSPL(data.spl) }}
        />
      </div>

      {/* Tabs Menu */}
      <div className="flex bg-[#121316] border-b border-splunk-border px-2">
        <button
          onClick={() => setActiveTab('results')}
          className={`px-4 py-2.5 text-xs font-medium border-b-2 cursor-pointer transition-colors ${
            activeTab === 'results'
              ? 'border-splunk-mint text-splunk-mint'
              : 'border-transparent text-splunk-text-muted hover:text-splunk-text-main'
          }`}
        >
          Results ({data.results.length})
        </button>
        <button
          onClick={() => setActiveTab('explanation')}
          className={`px-4 py-2.5 text-xs font-medium border-b-2 cursor-pointer transition-colors ${
            activeTab === 'explanation'
              ? 'border-splunk-mint text-splunk-mint'
              : 'border-transparent text-splunk-text-muted hover:text-splunk-text-main'
          }`}
        >
          Explanation
        </button>
        <button
          onClick={() => setActiveTab('actions')}
          className={`px-4 py-2.5 text-xs font-medium border-b-2 cursor-pointer transition-colors ${
            activeTab === 'actions'
              ? 'border-splunk-mint text-splunk-mint'
              : 'border-transparent text-splunk-text-muted hover:text-splunk-text-main'
          }`}
        >
          Priority Actions ({data.actions.length})
        </button>
      </div>

      {/* Tab Panels */}
      <div className="p-4">
        {/* Results Panel */}
        {activeTab === 'results' && (
          <div>
            {headers.length > 0 ? (
              <div className="overflow-x-auto max-h-[300px] overflow-y-auto rounded border border-splunk-border">
                <table className="w-full text-left text-xs font-sans border-collapse">
                  <thead>
                    <tr className="bg-splunk-bg-card border-b border-splunk-border">
                      {headers.map((h) => (
                        <th key={h} className="px-3 py-2 font-mono font-semibold text-splunk-text-main uppercase tracking-wider sticky top-0 bg-splunk-bg-card">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-splunk-border bg-[#141519]">
                    {data.results.slice(0, 10).map((row, i) => (
                      <tr key={i} className="hover:bg-splunk-bg-card/50 transition-colors">
                        {headers.map((h) => (
                          <td key={h} className="px-3 py-2 font-mono text-splunk-text-muted whitespace-nowrap overflow-hidden max-w-xs text-ellipsis">
                            {row[h]}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
                {data.results.length > 10 && (
                  <div className="p-2.5 bg-splunk-bg-card text-center text-[11px] text-splunk-text-muted border-t border-splunk-border">
                    Showing 10 of {data.results.length} rows (View full dataset in Inspector Panel)
                  </div>
                )}
              </div>
            ) : (
              <div className="py-6 text-center text-xs text-splunk-text-muted font-sans border border-dashed border-splunk-border rounded">
                No statistical events returned by query
              </div>
            )}

            {/* Micro Stats Footer */}
            <div className="flex justify-between items-center mt-3 text-[11px] font-mono text-splunk-text-muted">
              <div>
                Result Count: <span className="text-splunk-mint font-semibold">{data.metadata.result_count}</span>
              </div>
              <div>
                Duration: <span className="text-splunk-blue font-semibold">{data.metadata.execution_time_ms} ms</span>
              </div>
            </div>
          </div>
        )}

        {/* Explanation Panel */}
        {activeTab === 'explanation' && (
          <div className="text-xs text-splunk-text-muted leading-relaxed font-sans max-h-[300px] overflow-y-auto pr-2">
            <p className="whitespace-pre-wrap">{data.spl_explanation || "No explanation provided for this query."}</p>
          </div>
        )}

        {/* Actions Panel */}
        {activeTab === 'actions' && (
          <div className="space-y-3.5 max-h-[300px] overflow-y-auto pr-2">
            {data.actions.length > 0 ? (
              data.actions.map((act, i) => (
                <div 
                  key={i} 
                  onClick={() => onSelectAction?.(act)}
                  className={`p-3 border rounded-lg cursor-pointer transition-all hover:scale-[1.01] flex items-start gap-3 ${getRiskColor(act.risk_level)}`}
                >
                  <div className="p-1.5 rounded bg-black/10 mt-0.5">
                    {act.risk_level.toLowerCase() === 'high' || act.risk_level.toLowerCase() === 'critical' ? (
                      <ShieldAlert size={16} />
                    ) : act.risk_level.toLowerCase() === 'medium' ? (
                      <AlertTriangle size={16} />
                    ) : (
                      <CheckCircle2 size={16} />
                    )}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex justify-between items-baseline gap-2">
                      <span className="font-semibold text-xs text-splunk-text-main truncate">
                        {act.action}
                      </span>
                      <span className="text-[10px] uppercase font-mono tracking-wider font-semibold opacity-80">
                        {act.risk_level} RISK
                      </span>
                    </div>
                    <p className="text-[11px] text-splunk-text-muted mt-1 leading-normal font-sans">
                      Target: <span className="font-mono text-splunk-text-main">{act.target}</span>
                    </p>
                    <p className="text-[11px] text-splunk-text-muted/90 mt-1 leading-normal italic font-sans">
                      {act.reasoning}
                    </p>
                  </div>
                </div>
              ))
            ) : (
              <div className="py-6 text-center text-xs text-splunk-text-muted border border-dashed border-splunk-border rounded">
                No recommended security actions for this result
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
