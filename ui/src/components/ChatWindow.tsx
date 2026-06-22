import { useState, useRef, useEffect } from 'react';
import { sendQuery } from '../api/client';
import { MessageBubble } from './MessageBubble';
import type { Message } from './MessageBubble';
import './ChatWindow.css';

export function ChatWindow() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const queryText = input.trim();
    if (!queryText || loading) return;

    setInput('');
    setMessages((prev) => [...prev, { type: 'user', text: queryText }]);
    setLoading(true);

    try {
      const data = await sendQuery(queryText, sessionId);
      setSessionId(data.session_id);
      setMessages((prev) => [...prev, { type: 'system', data }]);
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'An error occurred';
      setMessages((prev) => [...prev, { type: 'error', text: errorMessage }]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="chat-window">
      <div className="messages-container">
        {messages.length === 0 && (
          <div className="empty-state">
            Ask a question about your Splunk data
          </div>
        )}
        {messages.map((msg, i) => (
          <MessageBubble key={i} message={msg} />
        ))}
        {loading && (
          <div className="message-row system-row">
            <div className="bubble system-bubble loading-bubble">
              Thinking...
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <form className="input-form" onSubmit={handleSubmit}>
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about your data..."
          disabled={loading}
          className="query-input"
        />
        <button type="submit" disabled={loading || !input.trim()} className="send-btn">
          Send
        </button>
      </form>
    </div>
  );
}
