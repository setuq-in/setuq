import { useState, useEffect, useRef } from 'react';
import { 
  Database, Menu, X, Search, RefreshCw, Send, ChevronRight, 
  Activity, Plus, Info, Terminal
} from 'lucide-react';
import { sendQuery, getSchema, refreshSchema } from './api/client';
import type { SchemaResponse, ActionSuggestion } from './api/client';
import { MessageBubble } from './components/MessageBubble';
import type { Message } from './components/MessageBubble';
import './App.css';

export default function App() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  
  // Layout views
  const [leftSidebarOpen, setLeftSidebarOpen] = useState(true);
  const [rightSidebarOpen, setRightSidebarOpen] = useState(true);
  const [rightTab, setRightTab] = useState<'inspector' | 'schema' | 'glossary'>('inspector');
  const [selectedMessage, setSelectedMessage] = useState<Message | null>(null);
  
  // Schema states
  const [schema, setSchema] = useState<SchemaResponse | null>(null);
  const [schemaSearch, setSchemaSearch] = useState('');
  const [loadingSchema, setLoadingSchema] = useState(false);

  // Stepper index for visual pipeline loader
  const [activeStep, setActiveStep] = useState(0);

  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Scroll to bottom whenever messages update
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading]);

  // Load schema
  useEffect(() => {
    async function loadSchema() {
      try {
        setLoadingSchema(true);
        const data = await getSchema();
        setSchema(data);
      } catch (err) {
        console.error("Failed to load schema", err);
      } finally {
        setLoadingSchema(false);
      }
    }
    loadSchema();
  }, []);

  // Cycle loader steps during query execution
  useEffect(() => {
    let interval: any;
    if (loading) {
      setActiveStep(0);
      interval = setInterval(() => {
        setActiveStep((prev) => (prev < 4 ? prev + 1 : prev));
      }, 1000);
    }
    return () => clearInterval(interval);
  }, [loading]);

  const handleNewSession = () => {
    setMessages([]);
    setSessionId(null);
    setSelectedMessage(null);
  };

  const handleSuggestedQuestion = (qText: string) => {
    setInput(qText);
  };

  const handleSubmit = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    const queryText = input.trim();
    if (!queryText || loading) return;

    setInput('');
    const userMsg: Message = { type: 'user', text: queryText };
    setMessages((prev) => [...prev, userMsg]);
    setLoading(true);

    try {
      const data = await sendQuery(queryText, sessionId);
      setSessionId(data.session_id);
      const systemMsg: Message = { type: 'system', data };
      setMessages((prev) => [...prev, systemMsg]);
      setSelectedMessage(systemMsg);
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : 'An error occurred';
      const errorMsg: Message = { type: 'error', text: errMsg };
      setMessages((prev) => [...prev, errorMsg]);
    } finally {
      setLoading(false);
    }
  };

  const handleRefreshSchema = async () => {
    try {
      setLoadingSchema(true);
      const data = await refreshSchema();
      setSchema(data);
    } catch (err) {
      console.error("Failed to refresh schema", err);
    } finally {
      setLoadingSchema(false);
    }
  };

  const handleFieldClick = (fieldName: string) => {
    setInput((prev) => (prev ? `${prev} ${fieldName}` : fieldName));
  };

  const handleActionClick = (act: ActionSuggestion) => {
    // Inject suggested action detail into prompt area
    setInput(`Investigate target ${act.target} for action: ${act.action}`);
  };

  // Pipeline loader steps metadata
  const loadingSteps = [
    { name: "planning", title: "Planning Investigation", desc: "Analyzing request security intent..." },
    { name: "spl", title: "SPL Generation", desc: "Generating Splunk search query..." },
    { name: "guardrails", title: "Query Guardrails", desc: "Evaluating search constraints..." },
    { name: "executing", title: "Executing SPL", desc: "Querying Splunk Search Head..." },
    { name: "analyzing", title: "Security Analysis", desc: "Evaluating anomalies & patterns..." }
  ];

  // Context mock history
  const activeContexts = [
    { id: "1", title: "Admin login abuse", time: "10m ago", severity: "high" },
    { id: "2", title: "Suspicious port scan", time: "1h ago", severity: "medium" },
    { id: "3", title: "Malicious process audit", time: "3h ago", severity: "critical" },
  ];

  // Suggested starter queries — grounded in the live Splunk schema
  // (chocolate_index/sales + _audit audittrailv2 + _internal)
  const exampleStarters = [
    "Show me top 10 products by revenue in the last 7 days",
    "Create a pie chart of revenue contribution by product name",
    "Show failed authentication attempts in the audit logs over the last 24 hours"
  ];

  // Helper to style risk labels
  const getRiskBadge = (risk: string) => {
    switch (risk.toLowerCase()) {
      case 'high':
      case 'critical':
        return 'bg-splunk-red/15 text-splunk-red border border-splunk-red/35';
      case 'medium':
        return 'bg-splunk-orange/15 text-splunk-orange border border-splunk-orange/35';
      default:
        return 'bg-splunk-mint/15 text-splunk-mint border border-splunk-mint/35';
    }
  };

  return (
    <div className="flex h-screen w-screen bg-splunk-bg-main text-splunk-text-main overflow-hidden font-sans select-none">
      
      {/* 1. LEFT SIDEBAR: Contexts & Search History */}
      <aside 
        className={`${
          leftSidebarOpen ? 'w-64' : 'w-0'
        } border-r border-splunk-border bg-splunk-bg-sidebar flex flex-col transition-all duration-300 overflow-hidden relative shrink-0 z-20`}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-4 border-b border-splunk-border/80">
          <div className="flex items-center gap-2.5">
            <img src="/setuq-mark.png" alt="Setuq" className="w-6 h-6 object-contain shrink-0" />
            <div className="flex flex-col">
              <span className="font-bold text-sm leading-none tracking-tight">SETUQ</span>
              <span className="text-[9px] text-splunk-text-muted font-mono leading-none mt-1">v1.2.0-stable</span>
            </div>
          </div>
          <button 
            onClick={() => setLeftSidebarOpen(false)}
            className="p-1 rounded hover:bg-splunk-bg-hover text-splunk-text-muted hover:text-splunk-text-main cursor-pointer"
          >
            <X size={15} />
          </button>
        </div>

        {/* Action Button */}
        <div className="p-3">
          <button 
            onClick={handleNewSession}
            className="w-full flex items-center justify-center gap-2 py-2 px-3 text-xs font-medium bg-splunk-bg-card border border-splunk-border rounded hover:bg-splunk-bg-hover hover:border-splunk-mint/55 text-splunk-text-main transition-colors cursor-pointer"
          >
            <Plus size={14} className="text-splunk-mint" />
            <span>New Investigation</span>
          </button>
        </div>

        {/* Content Lists */}
        <div className="flex-1 overflow-y-auto px-3 py-1 space-y-5">
          {/* Active Contexts */}
          <div>
            <span className="text-[10px] font-semibold text-splunk-text-muted tracking-wider uppercase px-2 select-none">
              Investigation Contexts
            </span>
            <div className="mt-2 space-y-1">
              {activeContexts.map((ctx) => (
                <div 
                  key={ctx.id}
                  onClick={() => handleSuggestedQuestion(`Audit session history for ${ctx.title}`)}
                  className="flex items-center justify-between p-2 rounded text-xs hover:bg-splunk-bg-card/75 border border-transparent hover:border-splunk-border cursor-pointer transition-all"
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                      ctx.severity === 'critical' ? 'bg-splunk-red animate-pulse' : ctx.severity === 'high' ? 'bg-splunk-orange' : 'bg-splunk-blue'
                    }`} />
                    <span className="truncate text-splunk-text-main font-medium">{ctx.title}</span>
                  </div>
                  <span className="text-[9px] font-mono text-splunk-text-muted shrink-0">{ctx.time}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Quick Shortcuts */}
          <div>
            <span className="text-[10px] font-semibold text-splunk-text-muted tracking-wider uppercase px-2 select-none">
              Suggested Starters
            </span>
            <div className="mt-2 space-y-1">
              {exampleStarters.map((starter, i) => (
                <button
                  key={i}
                  onClick={() => handleSuggestedQuestion(starter)}
                  className="w-full text-left p-2 rounded text-xs text-splunk-text-muted hover:text-splunk-text-main hover:bg-splunk-bg-card/65 transition-all truncate block cursor-pointer"
                >
                  {starter}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Footer info */}
        <div className="p-3 border-t border-splunk-border/70 bg-[#121316]/40 flex items-center justify-between text-[10px] font-mono text-splunk-text-muted">
          <span className="flex items-center gap-1">
            <Activity size={10} className="text-splunk-mint" />
            <span>Connection Live</span>
          </span>
          <span>head-01</span>
        </div>
      </aside>

      {/* Collapse Trigger for Left Sidebar */}
      {!leftSidebarOpen && (
        <button
          onClick={() => setLeftSidebarOpen(true)}
          className="fixed left-4 top-4 z-30 p-2 rounded bg-splunk-bg-sidebar border border-splunk-border text-splunk-text-muted hover:text-splunk-text-main shadow-lg cursor-pointer"
        >
          <Menu size={16} />
        </button>
      )}

      {/* 2. CENTER PANEL: Conversation Hub */}
      <main className="flex-1 flex flex-col h-full bg-splunk-bg-main relative min-w-0">
        
        {/* Top Navbar */}
        <header className="flex items-center justify-between px-6 py-4 border-b border-splunk-border/80 bg-splunk-bg-sidebar/40 shrink-0">
          <div className="flex items-center gap-2">
            {!leftSidebarOpen && <div className="w-8" />} {/* spacer when sidebar collapsed */}
            <h1 className="text-sm font-bold tracking-tight text-splunk-text-main flex items-center gap-2">
              <span>Setuq</span>
              <span className="text-[10px] font-mono font-normal px-2 py-0.5 rounded bg-splunk-mint/15 text-splunk-mint border border-splunk-mint/20">
                Agent Active
              </span>
            </h1>
          </div>
          
          <div className="flex items-center gap-3">
            <button 
              onClick={() => {
                setRightSidebarOpen(true);
                setRightTab('schema');
              }}
              className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs rounded border border-splunk-border bg-splunk-bg-card hover:bg-splunk-bg-hover text-splunk-text-main transition-colors cursor-pointer"
            >
              <Database size={13} className="text-splunk-mint" />
              <span>Browse Schema</span>
            </button>
            {!rightSidebarOpen && (
              <button
                onClick={() => setRightSidebarOpen(true)}
                className="p-1.5 rounded border border-splunk-border bg-splunk-bg-card text-splunk-text-muted hover:text-splunk-text-main cursor-pointer"
              >
                <ChevronRight size={14} className="rotate-180" />
              </button>
            )}
          </div>
        </header>

        {/* Messages Feed */}
        <div className="flex-1 overflow-y-auto px-6 py-6 scroll-smooth bg-radial-gradient">
          {messages.length === 0 && !loading && (
            <div className="h-full flex flex-col items-center justify-center max-w-lg mx-auto text-center select-none">
              <img src="/setuq-mark.png" alt="Setuq" className="w-16 h-16 object-contain mb-5 drop-shadow-lg" />
              <h2 className="text-base font-bold text-splunk-text-main">Autonomous Security Analysis</h2>
              <p className="text-xs text-splunk-text-muted mt-2 leading-relaxed">
                Welcome to Setuq &mdash; built to bridge. Splunk today, everything tomorrow. Ask questions across your Splunk indexes &mdash; business analytics (sales &amp; revenue) and audit-trail activity (authentication, account and configuration changes). The agent formulates multi-step search plans, executes SPL, analyzes trends and anomalies, and suggests next actions.
              </p>

              <div className="mt-8 w-full space-y-2">
                <span className="text-[10px] uppercase font-mono font-bold tracking-wider text-splunk-text-muted block text-left px-1">
                  Example inquiries:
                </span>
                {exampleStarters.map((starter, i) => (
                  <button
                    key={i}
                    onClick={() => handleSuggestedQuestion(starter)}
                    className="w-full text-left p-3 text-xs bg-splunk-bg-sidebar border border-splunk-border rounded-lg hover:border-splunk-mint/40 hover:bg-splunk-bg-hover transition-all cursor-pointer flex items-center justify-between"
                  >
                    <span>{starter}</span>
                    <ChevronRight size={12} className="text-splunk-text-muted" />
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Conversation Feed */}
          <div className="max-w-3xl mx-auto space-y-6">
            {messages.map((msg, i) => (
              <MessageBubble 
                key={i} 
                message={msg} 
                isSelected={selectedMessage === msg}
                onSelect={() => setSelectedMessage(msg)}
                onSelectAction={handleActionClick}
              />
            ))}

            {/* Premium Pipeline Loader State */}
            {loading && (
              <div className="flex gap-3.5 max-w-[92%] mr-auto mb-6">
                <div className="flex-shrink-0 w-8 h-8 rounded-full border border-splunk-mint/30 bg-splunk-mint/5 flex items-center justify-center text-splunk-mint animate-spin">
                  <Activity size={16} />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="bg-splunk-bg-sidebar border border-splunk-mint/30 rounded-2xl rounded-tl-none shadow-lg overflow-hidden">
                    <div className="p-4 bg-[#14161a]/60">
                      <div className="flex items-center justify-between">
                        <span className="text-xs font-bold text-splunk-text-main flex items-center gap-2">
                          <span>AGENT RUNNING PIPELINE</span>
                          <span className="w-1.5 h-1.5 rounded-full bg-splunk-mint animate-ping" />
                        </span>
                        <span className="text-[10px] font-mono text-splunk-text-muted">
                          Step {activeStep + 1} of 5
                        </span>
                      </div>
                      <p className="text-xs text-splunk-text-muted mt-1 font-sans">
                        {loadingSteps[activeStep]?.desc}
                      </p>
                    </div>

                    {/* Timeline Tracker */}
                    <div className="px-4 py-3 bg-splunk-bg-main/70 border-t border-splunk-border flex items-center justify-between gap-1 overflow-x-auto">
                      {loadingSteps.map((step, idx) => (
                        <div key={idx} className="flex items-center gap-1 shrink-0">
                          <div className={`w-4 h-4 rounded-full flex items-center justify-center text-[9px] font-bold ${
                            idx < activeStep 
                              ? 'bg-splunk-mint/20 border border-splunk-mint text-splunk-mint' 
                              : idx === activeStep 
                              ? 'bg-splunk-blue/20 border border-splunk-blue text-splunk-blue animate-pulse' 
                              : 'bg-splunk-bg-card border border-splunk-border text-splunk-text-muted'
                          }`}>
                            {idx + 1}
                          </div>
                          <span className={`text-[10px] font-medium font-sans ${
                            idx === activeStep ? 'text-splunk-text-main font-semibold' : 'text-splunk-text-muted'
                          }`}>
                            {step.title}
                          </span>
                          {idx < loadingSteps.length - 1 && (
                            <ChevronRight size={10} className="text-splunk-border mx-1" />
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>
        </div>

        {/* Bottom Search-Style Input Container */}
        <footer className="p-6 border-t border-splunk-border bg-splunk-bg-sidebar/55 shrink-0">
          <div className="max-w-3xl mx-auto">
            <form onSubmit={handleSubmit} className="relative flex items-center bg-[#090b0d] border border-splunk-border rounded-lg shadow-lg focus-within:border-splunk-mint/70 transition-all px-3 py-2">
              <Terminal size={16} className="text-splunk-text-muted mr-2.5 shrink-0" />
              <input
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="index=security sourcetype=win_auth | Ask your SOC query..."
                disabled={loading}
                className="flex-1 bg-transparent border-none outline-none font-mono text-sm py-1.5 text-splunk-text-main placeholder-splunk-text-muted/65 focus:ring-0 select-text disabled:opacity-60"
              />
              <button 
                type="submit" 
                disabled={loading || !input.trim()}
                className="ml-2 bg-splunk-mint hover:bg-emerald-600 disabled:bg-splunk-bg-card disabled:text-splunk-text-muted text-splunk-bg-main p-2 rounded cursor-pointer transition-all shrink-0 flex items-center justify-center"
              >
                <Send size={15} />
              </button>
            </form>
            <div className="mt-2 px-1 flex justify-between items-center text-[10px] font-mono text-splunk-text-muted">
              <span>Press Enter to analyze index telemetry</span>
              <span>Secure SOC sandbox environment</span>
            </div>
          </div>
        </footer>
      </main>

      {/* 3. RIGHT SIDEBAR: Inspector Panel */}
      <aside 
        className={`${
          rightSidebarOpen ? 'w-80' : 'w-0'
        } border-l border-splunk-border bg-splunk-bg-sidebar flex flex-col transition-all duration-300 overflow-hidden relative shrink-0 z-20`}
      >
        {/* Header Tabs */}
        <div className="flex border-b border-splunk-border bg-splunk-bg-sidebar shrink-0 justify-between items-center px-1">
          <div className="flex">
            <button
              onClick={() => setRightTab('inspector')}
              className={`px-3 py-3.5 text-xs font-semibold border-b-2 cursor-pointer transition-colors ${
                rightTab === 'inspector' 
                  ? 'border-splunk-mint text-splunk-mint' 
                  : 'border-transparent text-splunk-text-muted hover:text-splunk-text-main'
              }`}
            >
              Inspector
            </button>
            <button
              onClick={() => setRightTab('schema')}
              className={`px-3 py-3.5 text-xs font-semibold border-b-2 cursor-pointer transition-colors ${
                rightTab === 'schema' 
                  ? 'border-splunk-mint text-splunk-mint' 
                  : 'border-transparent text-splunk-text-muted hover:text-splunk-text-main'
              }`}
            >
              Schema
            </button>
            <button
              onClick={() => setRightTab('glossary')}
              className={`px-3 py-3.5 text-xs font-semibold border-b-2 cursor-pointer transition-colors ${
                rightTab === 'glossary' 
                  ? 'border-splunk-mint text-splunk-mint' 
                  : 'border-transparent text-splunk-text-muted hover:text-splunk-text-main'
              }`}
            >
              Glossary
            </button>
          </div>
          <button 
            onClick={() => setRightSidebarOpen(false)}
            className="p-1 rounded mr-2 hover:bg-splunk-bg-hover text-splunk-text-muted hover:text-splunk-text-main cursor-pointer"
          >
            <X size={15} />
          </button>
        </div>

        {/* Sidebar Tabs Content */}
        <div className="flex-1 overflow-y-auto p-4 select-text">
          
          {/* TAB: INSPECTOR */}
          {rightTab === 'inspector' && (
            <div className="space-y-5">
              {selectedMessage && selectedMessage.type === 'system' ? (
                <>
                  {/* Meta stats */}
                  <div className="p-3 bg-[#15171b] border border-splunk-border rounded-lg space-y-2">
                    <span className="text-[10px] font-bold text-splunk-mint uppercase block font-mono">
                      Query Metadata
                    </span>
                    <div className="grid grid-cols-2 gap-2.5 text-xs">
                      <div>
                        <span className="text-[10px] text-splunk-text-muted block">DURATION</span>
                        <span className="font-mono font-semibold text-splunk-text-main">
                          {selectedMessage.data.metadata.execution_time_ms} ms
                        </span>
                      </div>
                      <div>
                        <span className="text-[10px] text-splunk-text-muted block">RECORDS</span>
                        <span className="font-mono font-semibold text-splunk-text-main">
                          {selectedMessage.data.metadata.result_count} rows
                        </span>
                      </div>
                      <div className="col-span-2">
                        <span className="text-[10px] text-splunk-text-muted block">SESSION ID</span>
                        <span className="font-mono text-[10px] text-splunk-text-main select-all truncate block">
                          {selectedMessage.data.session_id}
                        </span>
                      </div>
                    </div>
                  </div>

                  {/* Decision Engine Recommendations */}
                  <div className="p-3 bg-splunk-bg-card border border-splunk-border rounded-lg space-y-3">
                    <span className="text-[10px] font-bold text-splunk-blue uppercase block font-mono">
                      Decision Recommendation
                    </span>
                    <div className="flex items-center justify-between">
                      <span className={`text-[10px] uppercase font-mono font-bold px-2 py-0.5 rounded ${
                        getRiskBadge(selectedMessage.data.decision.recommendation)
                      }`}>
                        {selectedMessage.data.decision.recommendation}
                      </span>
                      <div className="flex items-center gap-1 text-xs">
                        <span className="text-[10px] text-splunk-text-muted">CONFIDENCE:</span>
                        <span className="font-mono font-bold text-splunk-text-main">
                          {Math.round(selectedMessage.data.decision.confidence_score * 100)}%
                        </span>
                      </div>
                    </div>
                    <div>
                      <span className="text-[10px] text-splunk-text-muted block">REASONING</span>
                      <p className="text-xs text-splunk-text-muted/95 mt-1 font-sans leading-normal leading-relaxed">
                        {selectedMessage.data.decision.reasoning}
                      </p>
                    </div>
                  </div>

                  {/* Investigation Plan Steps */}
                  {selectedMessage.data.plan && selectedMessage.data.plan.needs_plan && (
                    <div className="p-3 bg-splunk-bg-card border border-splunk-border rounded-lg space-y-2">
                      <span className="text-[10px] font-bold text-splunk-orange uppercase block font-mono">
                        Pipeline Investigation Plan
                      </span>
                      <div className="space-y-3 mt-2">
                        {selectedMessage.data.plan.steps.map((step, idx) => (
                          <div key={idx} className="flex gap-2">
                            <span className="w-4 h-4 rounded-full bg-splunk-border text-[9px] font-mono flex items-center justify-center shrink-0 mt-0.5">
                              {idx + 1}
                            </span>
                            <div className="min-w-0">
                              <p className="text-xs text-splunk-text-main font-sans">{step.description}</p>
                              {step.spl_hint && (
                                <pre className="mt-1 p-1 bg-black/40 rounded font-mono text-[9px] text-splunk-text-muted overflow-x-auto">
                                  {step.spl_hint}
                                </pre>
                              )}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Analysis Anomalies */}
                  {selectedMessage.data.analysis && selectedMessage.data.analysis.anomalies.length > 0 && (
                    <div className="p-3 bg-splunk-bg-card border border-splunk-border rounded-lg space-y-2">
                      <span className="text-[10px] font-bold text-splunk-red uppercase block font-mono">
                        Identified Anomalies
                      </span>
                      <div className="space-y-3 mt-2">
                        {selectedMessage.data.analysis.anomalies.map((anom, idx) => (
                          <div key={idx} className="p-2 bg-black/25 rounded border border-splunk-border space-y-1">
                            <div className="flex justify-between items-center">
                              <span className="text-xs font-semibold text-splunk-text-main">Anomaly {idx + 1}</span>
                              <span className="text-[9px] font-mono font-bold text-splunk-red uppercase">
                                {anom.severity}
                              </span>
                            </div>
                            <p className="text-[11px] text-splunk-text-muted font-sans leading-normal">{anom.description}</p>
                            <p className="text-[10px] font-mono text-splunk-text-muted italic">Evidence: {anom.evidence}</p>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </>
              ) : (
                <div className="flex flex-col items-center justify-center py-12 text-center text-splunk-text-muted">
                  <Info size={24} className="text-splunk-border mb-2.5" />
                  <span className="text-xs font-sans">No response message selected.</span>
                  <span className="text-[10px] text-splunk-text-muted/85 mt-1">Click a message bubble to audit its telemetry.</span>
                </div>
              )}
            </div>
          )}

          {/* TAB: SCHEMA */}
          {rightTab === 'schema' && (
            <div className="space-y-4">
              <div className="flex items-center justify-between shrink-0">
                <span className="text-[10px] font-bold text-splunk-text-muted uppercase tracking-wider">
                  Indexed Schemas
                </span>
                <button 
                  onClick={handleRefreshSchema}
                  disabled={loadingSchema}
                  className="p-1 rounded hover:bg-splunk-bg-hover text-splunk-text-muted hover:text-splunk-text-main cursor-pointer"
                >
                  <RefreshCw size={13} className={loadingSchema ? 'animate-spin' : ''} />
                </button>
              </div>

              {/* Search */}
              <div className="relative flex items-center bg-[#0d0f12] border border-splunk-border rounded px-2.5 py-1.5 focus-within:border-splunk-mint/55 transition-all">
                <Search size={13} className="text-splunk-text-muted mr-2 shrink-0" />
                <input
                  type="text"
                  value={schemaSearch}
                  onChange={(e) => setSchemaSearch(e.target.value)}
                  placeholder="Filter indexes or fields..."
                  className="bg-transparent border-none outline-none text-xs w-full text-splunk-text-main placeholder-splunk-text-muted/70"
                />
              </div>

              {/* Index Tree */}
              <div className="space-y-4 mt-2">
                {schema?.indexes ? (
                  Object.keys(schema.indexes)
                    .filter(idx => idx.toLowerCase().includes(schemaSearch.toLowerCase()))
                    .map((idxName) => {
                      const idx = schema.indexes[idxName];
                      return (
                        <div key={idxName} className="p-3 bg-[#131417] border border-splunk-border rounded-lg space-y-2">
                          <div className="flex justify-between items-center">
                            <span className="font-mono font-bold text-xs text-splunk-mint">{idxName}</span>
                            <span className="text-[9px] font-mono text-splunk-text-muted uppercase px-1.5 py-0.5 rounded bg-splunk-bg-card border border-splunk-border">
                              {idx.role || 'user'}
                            </span>
                          </div>
                          {idx.description && (
                            <p className="text-[11px] text-splunk-text-muted font-sans leading-normal">
                              {idx.description}
                            </p>
                          )}
                          
                          {/* Sourcetypes */}
                          <div className="mt-2 pt-2 border-t border-splunk-border/40 space-y-2">
                            {Object.keys(idx.sourcetypes).map((stName) => {
                              const st = idx.sourcetypes[stName];
                              return (
                                <div key={stName} className="space-y-1">
                                  <span className="text-[10px] font-mono text-splunk-blue block font-semibold">{stName}</span>
                                  {st.fields && (
                                    <div className="flex flex-wrap gap-1 mt-1">
                                      {st.fields.map((f, i) => (
                                        <button
                                          key={i}
                                          onClick={() => handleFieldClick(typeof f === 'string' ? f : f.name)}
                                          title={typeof f === 'object' ? f.description : undefined}
                                          className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-splunk-bg-card hover:bg-splunk-bg-hover text-splunk-text-muted border border-splunk-border cursor-pointer transition-colors"
                                        >
                                          {typeof f === 'string' ? f : f.name}
                                        </button>
                                      ))}
                                    </div>
                                  )}
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      );
                    })
                ) : (
                  <div className="py-6 text-center text-xs text-splunk-text-muted">
                    No active index schema loaded
                  </div>
                )}
              </div>
            </div>
          )}

          {/* TAB: GLOSSARY */}
          {rightTab === 'glossary' && (
            <div className="space-y-4">
              <div>
                <span className="text-[10px] font-bold text-splunk-text-muted uppercase tracking-wider block">
                  Business Glossaries
                </span>
                <span className="text-[9px] text-splunk-text-muted font-sans mt-0.5 block">
                  Term mappings defined in override policies.
                </span>
              </div>

              <div className="space-y-2">
                {schema?.glossary ? (
                  schema.glossary.map((item, idx) => (
                    <div key={idx} className="p-3 bg-[#131417] border border-splunk-border rounded-lg space-y-1.5">
                      <span className="font-semibold text-xs text-splunk-text-main font-sans">
                        "{item.term}"
                      </span>
                      <ChevronRight size={10} className="text-splunk-text-muted inline mx-1" />
                      <span className="font-mono text-xs text-splunk-mint select-all">
                        {item.maps_to}
                      </span>
                    </div>
                  ))
                ) : (
                  <div className="py-6 text-center text-xs text-splunk-text-muted">
                    No glossary items found
                  </div>
                )}
              </div>

              {/* Investigation patterns */}
              {schema?.investigation_patterns && (
                <div className="mt-6 space-y-3">
                  <span className="text-[10px] font-bold text-splunk-text-muted uppercase tracking-wider block">
                    SOC Playbook Patterns
                  </span>
                  {schema.investigation_patterns.map((pat, idx) => (
                    <div key={idx} className="p-3 bg-[#131417] border border-splunk-border rounded-lg space-y-1.5">
                      <span className="font-semibold text-xs text-splunk-text-main block">
                        {pat.name}
                      </span>
                      <span className="text-[9px] font-mono text-splunk-text-muted block">
                        Applies to: {pat.applies_to}
                      </span>
                      <ol className="list-decimal pl-4 space-y-1 text-[10px] text-splunk-text-muted font-sans mt-1">
                        {pat.steps.map((st, i) => (
                          <li key={i}>{st}</li>
                        ))}
                      </ol>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

        </div>
      </aside>

    </div>
  );
}
