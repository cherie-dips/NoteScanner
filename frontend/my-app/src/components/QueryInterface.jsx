import { useState, useRef, useEffect, useCallback } from "react";
import { LuSendHorizontal, LuPlus, LuMic, LuRotateCcw } from "react-icons/lu";
import Markdown from "./Markdown";
import { API_BASE } from "../config";
import { authFetch, apiErrorMessage, apiRequest, errorText } from "../auth";
import { loadHistory, saveHistory, clearHistory } from "../chatHistory";

const makeSessionId = () =>
  (typeof crypto !== "undefined" && crypto.randomUUID
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`);

const makeMessageId = () => `m-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

/** Path a chat source points at, or "" if it can't be opened (e.g. a + chat upload). */
function sourcePath(doc) {
  const md = doc?.metadata || {};
  const p = String(md.path || "");
  if (!p || md.ephemeral_chat_upload || p.startsWith("[chat-upload] ")) return "";
  return p;
}

/** "file.pdf · page 3": the label shown for a chat source. */
function sourceLabel(doc) {
  const md = doc?.metadata || {};
  return md.source_file || md.path || "chunk";
}

function isErrorAnswer(msg) {
  return /^Error:/.test(msg.content || "");
}

export default function QueryInterface({
  activeFilePath = "",
  activeCourseFolder = "",
  onOpenSource,
  highlight = "",
  onClearHighlight,
}) {
  const [initial] = useState(loadHistory);
  const [query, setQuery] = useState("");
  const [messages, setMessages] = useState(() => initial?.messages || []);
  const [loading, setLoading] = useState(false);
  const [listening, setListening] = useState(false);
  const [chatSessionId, setChatSessionId] = useState(() => initial?.chatSessionId || makeSessionId());
  const messagesEndRef = useRef(null);
  const uploadInputRef = useRef(null);
  const recognitionRef = useRef(null);
  const abortRef = useRef(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };
  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  // Save the conversation (not on every streamed word: only once an answer is complete).
  useEffect(() => {
    if (loading) return;
    saveHistory(chatSessionId, messages);
  }, [messages, loading, chatSessionId]);

  useEffect(() => {
    return () => {
      if (recognitionRef.current) {
        recognitionRef.current.stop();
        recognitionRef.current = null;
      }
      abortRef.current?.abort();
    };
  }, []);

  const updateMessage = useCallback((id, patch) => {
    setMessages((prev) =>
      prev.map((m) => (m.id === id ? { ...m, ...(typeof patch === "function" ? patch(m) : patch) } : m)),
    );
  }, []);

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
      throw new Error(apiErrorMessage(data, res));
    }
    const chars = data?.text_chars;
    const suffix = Number.isFinite(chars) ? ` (${chars} chars cached)` : "";
    const note = data?.text_truncated
      ? " The file was very long, so only the first part will be searched."
      : "";
    setMessages((prev) => [
      ...prev,
      {
        id: makeMessageId(),
        role: "assistant",
        content: `Uploaded ${file.name} for this chat session${suffix}.${note}`,
      },
    ]);
  };

  const handleVoiceInput = () => {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      setMessages((prev) => [
        ...prev,
        { id: makeMessageId(), role: "assistant", content: "Voice input is not supported in this browser." },
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

  const buildQueryForm = (text, selection = "") => {
    // The server searches in order: chat uploads, open file, same course, then all other notes.
    const formData = new FormData();
    formData.append("query", text);
    if (selection) formData.append("highlight", selection);
    formData.append("opened_file_path", activeFilePath || "");
    formData.append("course_path", activeCourseFolder || "");
    formData.append("include_course_context", "true");
    formData.append("chat_session_id", chatSessionId);
    return formData;
  };

  /** Older servers without the streaming route: one request, whole answer at once. */
  const askWithoutStreaming = async (text, selection, msgId, signal) => {
    const res = await authFetch(`${API_BASE}/query_folder`, {
      method: "POST",
      body: buildQueryForm(text, selection),
      signal,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.error) {
      updateMessage(msgId, { content: `Error: ${apiErrorMessage(data, res)}`, streaming: false });
      return;
    }
    updateMessage(msgId, {
      content: data.answer || "",
      source_documents: data.source_documents || [],
      selection_stage: data.selection_stage || "",
      streaming: false,
    });
  };

  /** Read the NDJSON stream: a "meta" line with sources, then "delta" lines with answer text. */
  const readAnswerStream = async (res, msgId) => {
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let finished = false;
    let gotText = false;

    const handleLine = (line) => {
      if (!line.trim()) return;
      let evt;
      try {
        evt = JSON.parse(line);
      } catch {
        return;
      }
      if (evt.type === "meta") {
        updateMessage(msgId, {
          source_documents: evt.source_documents || [],
          selection_stage: evt.selection_stage || "",
        });
      } else if (evt.type === "delta" && evt.text) {
        gotText = true;
        updateMessage(msgId, (m) => ({ content: (m.content || "") + evt.text }));
      } else if (evt.type === "done") {
        finished = true;
      } else if (evt.type === "error") {
        finished = true;
        const msg = evt.error || "The AI couldn't finish the answer.";
        updateMessage(msgId, (m) => ({
          content: m.content ? `${m.content}\n\n_(Answer interrupted: ${msg})_` : `Error: ${msg}`,
        }));
      }
    };

    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      lines.forEach(handleLine);
    }
    buffer += decoder.decode();
    handleLine(buffer);

    if (!finished) {
      updateMessage(msgId, (m) => ({
        content: gotText
          ? `${m.content}\n\n_(The connection dropped before the answer finished.)_`
          : "Error: The answer was interrupted. Please try again.",
      }));
    }
    updateMessage(msgId, { streaming: false });
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    const text = query.trim();
    if (!text || loading) return;

    const msgId = makeMessageId();
    const selection = (highlight || "").trim();
    setQuery("");
    if (selection) onClearHighlight?.();
    setMessages((prev) => [
      ...prev,
      { id: makeMessageId(), role: "user", content: text, highlight: selection || undefined },
      {
        id: msgId,
        role: "assistant",
        content: "",
        source_documents: [],
        streaming: true,
        question: text,
      },
    ]);
    setLoading(true);
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const res = await authFetch(`${API_BASE}/query_folder/stream`, {
        method: "POST",
        body: buildQueryForm(text, selection),
        signal: controller.signal,
      });
      if (res.status === 404 || res.status === 405) {
        await askWithoutStreaming(text, selection, msgId, controller.signal);
      } else if (!res.ok || !res.body) {
        const data = await res.json().catch(() => ({}));
        updateMessage(msgId, { content: `Error: ${apiErrorMessage(data, res)}`, streaming: false });
      } else {
        await readAnswerStream(res, msgId);
      }
    } catch (err) {
      if (err?.name === "AbortError") {
        updateMessage(msgId, (m) => ({ content: m.content || "(Stopped.)", streaming: false }));
      } else {
        console.error("Query error:", err);
        updateMessage(msgId, { content: `Error: ${errorText(err)}`, streaming: false });
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      setLoading(false);
    }
  };

  /** Rate an answer. 👍 is sent at once; 👎 first asks "What was wrong?" (sendFeedback sends it). */
  const sendFeedback = async (msg, rating, comment = "") => {
    updateMessage(msg.id, { rating, feedbackState: "sending" });
    try {
      const sources = (msg.source_documents || [])
        .map((d) => d?.metadata?.path || "")
        .filter(Boolean);
      await apiRequest("/feedback", {
        method: "POST",
        form: {
          kind: "answer",
          rating,
          comment,
          question: msg.question || "",
          answer: (msg.content || "").slice(0, 2000),
          sources: [...new Set(sources)].join(","),
          selection_stage: msg.selection_stage || "",
        },
      });
      updateMessage(msg.id, { feedbackState: "sent", feedbackError: "" });
    } catch (err) {
      updateMessage(msg.id, {
        feedbackState: rating === "down" ? "comment" : null,
        feedbackError: errorText(err),
      });
    }
  };

  const renderRating = (msg) => {
    if (msg.role !== "assistant" || msg.streaming || !msg.question || !msg.content || isErrorAnswer(msg)) {
      return null;
    }
    if (msg.feedbackState === "sent") {
      return <div className="answer-feedback answer-feedback-thanks">Thanks for the feedback!</div>;
    }
    if (msg.feedbackState === "comment" || (msg.feedbackState === "sending" && msg.rating === "down")) {
      return (
        <form
          className="answer-feedback answer-feedback-form"
          onSubmit={(e) => {
            e.preventDefault();
            void sendFeedback(msg, "down", (msg.feedbackDraft || "").trim());
          }}
        >
          <input
            type="text"
            className="answer-feedback-input"
            placeholder="What was wrong? (optional)"
            aria-label="What was wrong with this answer?"
            maxLength={1000}
            value={msg.feedbackDraft || ""}
            onChange={(e) => updateMessage(msg.id, { feedbackDraft: e.target.value })}
            autoFocus
          />
          <button type="submit" className="answer-feedback-send" disabled={msg.feedbackState === "sending"}>
            {msg.feedbackState === "sending" ? "Sending…" : "Send"}
          </button>
          {msg.feedbackError && <span className="answer-feedback-error">{msg.feedbackError}</span>}
        </form>
      );
    }
    return (
      <div className="answer-feedback" role="group" aria-label="Rate this answer">
        <button
          type="button"
          className="answer-rate-btn answer-rate-up"
          onClick={() => sendFeedback(msg, "up")}
          disabled={msg.feedbackState === "sending"}
          aria-label="Helpful answer"
          title="Helpful"
        >
          👍
        </button>
        <button
          type="button"
          className="answer-rate-btn answer-rate-down"
          onClick={() => updateMessage(msg.id, { rating: "down", feedbackState: "comment", feedbackError: "" })}
          disabled={msg.feedbackState === "sending"}
          aria-label="Not helpful"
          title="Not helpful"
        >
          👎
        </button>
        {msg.feedbackError && <span className="answer-feedback-error">{msg.feedbackError}</span>}
      </div>
    );
  };

  const startNewChat = async () => {
    abortRef.current?.abort();
    const fd = new FormData();
    fd.append("chat_session_id", chatSessionId);
    authFetch(`${API_BASE}/chat/session/clear`, { method: "POST", body: fd }).catch(() => {});
    clearHistory();
    setMessages([]);
    setChatSessionId(makeSessionId());
  };

  return (
    <div className="query-interface">
      <div className="query-messages">
        {messages.length === 0 && (
          <div className="query-messages-empty">Ask a question about your notes.</div>
        )}
        {messages.map((msg, index) => (
          <div key={msg.id || index} className={`query-message query-message--${msg.role}`}>
            <div
              className={`query-message-bubble${msg.streaming && !msg.content ? " query-message-loading" : ""}`}
            >
              {msg.role === "assistant" ? (
                msg.streaming && !msg.content ? (
                  <div className="query-message-content">Thinking…</div>
                ) : (
                  <Markdown className="query-message-content">{msg.content || ""}</Markdown>
                )
              ) : (
                <>
                  {msg.highlight && (
                    <blockquote className="query-message-highlight">
                      “{msg.highlight.length > 160 ? `${msg.highlight.slice(0, 160)}…` : msg.highlight}”
                    </blockquote>
                  )}
                  <div className="query-message-content">{msg.content}</div>
                </>
              )}
              {msg.role === "assistant" && msg.source_documents?.length > 0 ? (
                <div className="query-sources">
                  <div className="query-sources-title">Sources</div>
                  {msg.source_documents.map((doc, i) => {
                    const path = sourcePath(doc);
                    const page = Number(doc.metadata?.page);
                    const body = (
                      <>
                        <div className="query-source-meta">
                          {sourceLabel(doc)}
                          {Number.isFinite(page) && page > 0 && (
                            <span className="query-source-page"> · page {page}</span>
                          )}
                          {doc.metadata?.is_primary_authority ? " · main source" : ""}
                        </div>
                        {doc.content?.substring(0, 200)}…
                      </>
                    );
                    return path && onOpenSource ? (
                      <button
                        key={i}
                        type="button"
                        className="query-source-item query-source-item--link"
                        onClick={() => onOpenSource(path)}
                        title={`Open ${path}`}
                      >
                        {body}
                      </button>
                    ) : (
                      <div key={i} className="query-source-item">
                        {body}
                      </div>
                    );
                  })}
                </div>
              ) : null}
              {renderRating(msg)}
            </div>
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      <form onSubmit={handleSubmit} className="query-form query-form-bottom">
        <label className="query-form-label-sr">Query your notes</label>
        {highlight && (
          <div className="query-highlight-chip" title={highlight}>
            <span className="query-highlight-text">
              Asking about: “{highlight.length > 80 ? `${highlight.slice(0, 80)}…` : highlight}”
            </span>
            <button
              type="button"
              className="query-highlight-clear"
              onClick={() => onClearHighlight?.()}
              aria-label="Stop asking about the selected text"
              title="Remove"
            >
              ×
            </button>
          </div>
        )}
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
            title="New chat"
            aria-label="Refresh chat"
            onClick={startNewChat}
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
              { id: makeMessageId(), role: "assistant", content: `Upload failed: ${errorText(err)}` },
            ]);
          }
        }}
      />
    </div>
  );
}
