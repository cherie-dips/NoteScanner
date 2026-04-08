import { useState, useRef, useEffect } from "react";
import { LuSendHorizontal, LuPlus, LuMic, LuRotateCcw } from "react-icons/lu";
import { API_BASE } from "../config";
import { authFetch } from "../auth";

export default function QueryInterface({
  coursesCsv = "",
  activeFilePath = "",
  activeCourseFolder = "",
}) {
  const makeSessionId = () =>
    (typeof crypto !== "undefined" && crypto.randomUUID
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`);
  const [query, setQuery] = useState("");
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [listening, setListening] = useState(false);
  const [chatSessionId, setChatSessionId] = useState(makeSessionId);
  const messagesEndRef = useRef(null);
  const uploadInputRef = useRef(null);
  const recognitionRef = useRef(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };
  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  useEffect(() => {
    return () => {
      if (recognitionRef.current) {
        recognitionRef.current.stop();
        recognitionRef.current = null;
      }
      const fd = new FormData();
      fd.append("chat_session_id", chatSessionId);
      authFetch(`${API_BASE}/chat/session/clear`, { method: "POST", body: fd }).catch(() => {});
    };
  }, [chatSessionId]);

  const handleUpload = async (file) => {
    if (!file) return;
    const formData = new FormData();
    formData.append("chat_session_id", chatSessionId);
    formData.append("file", file);
    const res = await authFetch(`${API_BASE}/chat/upload_ephemeral`, {
      method: "POST",
      body: formData,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || data.error || "Upload failed.");
    }
    const chars = data?.text_chars;
    const suffix = Number.isFinite(chars) ? ` (${chars} chars cached)` : "";
    setMessages((prev) => [
      ...prev,
      { role: "assistant", content: `Uploaded ${file.name} for this chat session${suffix}.` },
    ]);
  };

  const handleVoiceInput = () => {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: "Voice input is not supported in this browser." },
      ]);
      return;
    }
    if (listening && recognitionRef.current) {
      recognitionRef.current.stop();
      return;
    }
    const rec = new SpeechRecognition();
    rec.lang = "en-US";
    rec.interimResults = false;
    rec.maxAlternatives = 1;
    rec.onstart = () => setListening(true);
    rec.onresult = (event) => {
      const spoken = event?.results?.[0]?.[0]?.transcript || "";
      if (spoken.trim()) {
        setQuery((prev) => (prev ? `${prev} ${spoken}` : spoken));
      }
    };
    rec.onerror = () => setListening(false);
    rec.onend = () => {
      setListening(false);
      recognitionRef.current = null;
    };
    recognitionRef.current = rec;
    rec.start();
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    const text = query.trim();
    if (!text) return;

    setQuery("");
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setLoading(true);

    try {
      const includeCourseByPrompt = /\b(course notes|other notes|same course|all notes|include course|include other)\b/i.test(text);
      const formData = new FormData();
      formData.append("query", text);
      formData.append("subject", "");
      formData.append("courses", coursesCsv);
      formData.append("opened_file_path", activeFilePath || "");
      formData.append("course_path", activeCourseFolder || "");
      formData.append("include_course_context", includeCourseByPrompt ? "true" : "false");
      formData.append("use_course_notes", includeCourseByPrompt ? "true" : "false");
      formData.append("chat_session_id", chatSessionId);

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
        {
          role: "assistant",
          content: answer,
          source_documents,
        },
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
              {msg.role === "assistant" && msg.source_documents?.length > 0 ? (
                <div className="query-sources">
                  <div className="query-sources-title">Sources</div>
                  {msg.source_documents.map((doc, i) => (
                    <div key={i} className="query-source-item">
                      <div className="query-source-meta">
                        {(doc.metadata?.source_file || doc.metadata?.path || "chunk") +
                          (doc.metadata?.is_primary_authority ? " · primary" : "")}
                      </div>
                      {doc.content?.substring(0, 200)}…
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          </div>
        ))}
        {loading && (
          <div className="query-message query-message--assistant">
            <div className="query-message-bubble query-message-loading">
              <div className="query-message-content">Thinking…</div>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <form onSubmit={handleSubmit} className="query-form query-form-bottom">
        <label className="query-form-label-sr">Query your notes</label>
        <div className="query-input-wrap">
          <button
            type="button"
            className="query-input-icon"
            title="Add files/images"
            aria-label="Add files/images"
            onClick={() => uploadInputRef.current?.click()}
            disabled={loading}
          >
            <LuPlus size={18} />
          </button>
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
            type="button"
            className={`query-input-icon ${listening ? "query-input-icon--active" : ""}`}
            title={listening ? "Stop voice input" : "Voice input"}
            aria-label={listening ? "Stop voice input" : "Voice input"}
            onClick={handleVoiceInput}
            disabled={loading}
          >
            <LuMic size={18} />
          </button>
          <button
            type="button"
            className="query-input-icon"
            title="Refresh chat"
            aria-label="Refresh chat"
            onClick={async () => {
              const fd = new FormData();
              fd.append("chat_session_id", chatSessionId);
              await authFetch(`${API_BASE}/chat/session/clear`, { method: "POST", body: fd }).catch(() => {});
              setMessages([]);
              setChatSessionId(makeSessionId());
            }}
            disabled={loading}
          >
            <LuRotateCcw size={18} />
          </button>
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
      <input
        ref={uploadInputRef}
        type="file"
        accept="image/*,.pdf,.txt,.md,.csv,.json"
        style={{ display: "none" }}
        onChange={async (e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (!file) return;
          try {
            await handleUpload(file);
          } catch (err) {
            setMessages((prev) => [
              ...prev,
              { role: "assistant", content: `Upload failed: ${err?.message || "Unknown error."}` },
            ]);
          }
        }}
      />
    </div>
  );
}
