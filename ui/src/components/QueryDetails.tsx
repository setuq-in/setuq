import { useState, useEffect } from 'react';
import { Copy, Terminal, ExternalLink, ShieldAlert, CheckCircle2, AlertTriangle, BarChart3 } from 'lucide-react';
import type { QueryResponse, ActionSuggestion, ChartType, SplunkChartExport } from '../api/client';
import { exportChart } from '../api/client';
import { ChartRenderer } from './charts/ChartRenderer';
import { CHART_TYPES } from './charts/chartOptions';

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
  const [activeTab, setActiveTab] = useState<'results' | 'explanation' | 'actions' | 'chart'>('results');
  const [chartType, setChartType] = useState<ChartType | null>(data.chart_spec?.chart_type ?? null);
  const [exportFormat, setExportFormat] = useState<'xml' | 'json'>('xml');
  const [exportData, setExportData] = useState<SplunkChartExport | null>(null);
  const [exportError, setExportError] = useState(false);
  const [splunkCopied, setSplunkCopied] = useState(false);

  const effectiveSpec =
    data.chart_spec && chartType
      ? { ...data.chart_spec, chart_type: chartType }
      : data.chart_spec;

  // Fetch Splunk export source whenever the chart tab is open and the type changes.
  useEffect(() => {
    if (activeTab !== 'chart' || !effectiveSpec) return;
    let cancelled = false;
    setExportError(false);
    exportChart(data.spl, effectiveSpec)
      .then((res) => !cancelled && setExportData(res))
      .catch(() => !cancelled && setExportError(true));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, chartType, data.spl]);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(data.spl);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleCopySplunk = async () => {
    if (!exportData) return;
    const text = exportFormat === 'xml' ? exportData.simple_xml : exportData.studio_json;
    await navigator.clipboard.writeText(text);
    setSplunkCopied(true);
    setTimeout(() => setSplunkCopied(false), 2000);
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
        {data.chart_spec && (
          <button
            onClick={() => setActiveTab('chart')}
            className={`flex items-center gap-1.5 px-4 py-2.5 text-xs font-medium border-b-2 cursor-pointer transition-colors ${
              activeTab === 'chart'
                ? 'border-splunk-mint text-splunk-mint'
                : 'border-transparent text-splunk-text-muted hover:text-splunk-text-main'
            }`}
          >
            <BarChart3 size={13} />
            Chart
          </button>
        )}
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

        {/* Chart Panel */}
        {activeTab === 'chart' && effectiveSpec && (
          <div className="space-y-4">
            {/* Chart type picker */}
            <div className="flex items-center gap-2">
              <label className="text-[11px] font-mono uppercase tracking-wider text-splunk-text-muted">
                Chart Type
              </label>
              <select
                value={effectiveSpec.chart_type}
                onChange={(e) => setChartType(e.target.value as ChartType)}
                className="text-xs font-sans bg-splunk-bg-card border border-splunk-border rounded px-2 py-1 text-splunk-text-main cursor-pointer"
              >
                {CHART_TYPES.map((t) => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            </div>

            {/* Live preview */}
            <div className="rounded border border-splunk-border bg-[#0c0d10] p-2">
              <ChartRenderer spec={effectiveSpec} rows={data.results} size="full" />
            </div>

            {/* Copy to Splunk */}
            <div className="rounded border border-splunk-border bg-splunk-bg-sidebar overflow-hidden">
              <div className="flex items-center justify-between px-3 py-2 border-b border-splunk-border">
                <div className="flex items-center gap-2 text-[11px] font-mono uppercase tracking-wider text-splunk-text-muted">
                  <span>Paste into Splunk</span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="flex rounded border border-splunk-border overflow-hidden">
                    {(['xml', 'json'] as const).map((fmt) => (
                      <button
                        key={fmt}
                        onClick={() => setExportFormat(fmt)}
                        className={`px-2.5 py-1 text-[11px] font-medium cursor-pointer transition-colors ${
                          exportFormat === fmt
                            ? 'bg-splunk-blue text-white'
                            : 'bg-splunk-bg-card text-splunk-text-muted hover:text-splunk-text-main'
                        }`}
                      >
                        {fmt === 'xml' ? 'Simple XML' : 'Studio JSON'}
                      </button>
                    ))}
                  </div>
                  <button
                    onClick={handleCopySplunk}
                    disabled={!exportData}
                    className="flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium rounded border border-splunk-border bg-splunk-bg-card hover:bg-splunk-bg-hover text-splunk-text-main transition-colors cursor-pointer disabled:opacity-50"
                  >
                    <Copy size={12} />
                    <span>{splunkCopied ? 'Copied' : 'Copy'}</span>
                  </button>
                </div>
              </div>
              <div className="p-3 bg-[#08090b] overflow-auto max-h-[260px]">
                {exportError ? (
                  <p className="text-xs text-splunk-red font-sans">Failed to generate Splunk source.</p>
                ) : exportData ? (
                  <pre className="font-mono text-[11px] leading-relaxed whitespace-pre text-splunk-text-muted select-all">
                    {exportFormat === 'xml' ? exportData.simple_xml : exportData.studio_json}
                  </pre>
                ) : (
                  <p className="text-xs text-splunk-text-muted font-sans">Generating…</p>
                )}
              </div>
              {exportData && exportData.notes.length > 0 && (
                <div className="px-3 py-2 border-t border-splunk-border text-[11px] text-splunk-orange font-sans">
                  {exportData.notes.map((n, i) => (
                    <p key={i}>⚠ {n}</p>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
