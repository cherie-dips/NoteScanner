import { useState, useRef, useEffect } from "react";
import { LuSendHorizontal } from "react-icons/lu";
import { API_BASE } from "../config";
import { authFetch } from "../auth";

export default function QueryInterface() {
  const [query, setQuery] = useState("");
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const messagesEndRef = useRef(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };
  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    const text = query.trim();
    if (!text) return;

    setQuery("");
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setLoading(true);

    try {
      const formData = new FormData();
      formData.append("query", text);
      formData.append("subject", "notes");

      const res = await authFetch(`${API_BASE}/query_folder`, {
        method: "POST",
        body: formData,
      });

      const data = await res.json().catch(() => ({}));
      const errMsg = data.detail || data.error;
      const answer = res.ok && !errMsg
        ? data.answer
        : (errMsg ? `Error: ${errMsg}` : "Query failed. Please try again.");
      const source_documents = data.source_documents || [];

      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: answer, source_documents },
      ]);
    } catch (err) {
      console.error("Query error:", err);
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: "Query failed. Please try again.", source_documents: [] },
      ]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="query-interface">
      <div className="query-messages">
        {messages.length === 0 && (
          <div className="query-messages-empty">Ask a question about your notes.</div>
        )}
        {messages.map((msg, index) => (
          <div key={index} className={`query-message query-message--${msg.role}`}>
            <div className="query-message-bubble">
              <div className="query-message-content">{msg.content}</div>
              {msg.role === "assistant" && msg.source_documents?.length > 0 && (
                <div className="query-sources">
                  <div className="query-sources-title">Sources</div>
                  {msg.source_documents.map((doc, i) => (
                    <div key={i} className="query-source-item">
                      {doc.content?.substring(0, 200)}...
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
        {loading && (
          <div className="query-message query-message--assistant">
            <div className="query-message-bubble query-message-loading">
              <div className="query-message-content">Thinking...</div>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <form onSubmit={handleSubmit} className="query-form query-form-bottom">
        <label className="query-form-label-sr">Query your notes</label>
        <div className="query-input-wrap">
          <textarea
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                if (query.trim()) handleSubmit(e);
              }
            }}
            placeholder="Query your notes"
            rows={1}
            className="query-form-textarea query-form-textarea-inline"
            disabled={loading}
            aria-label="Query your notes"
          />
          <button
            type="submit"
            disabled={loading || !query.trim()}
            className="query-submit-icon"
            title="Send"
            aria-label="Send query"
          >
            <LuSendHorizontal size={20} />
          </button>
        </div>
      </form>
    </div>
  );
}
