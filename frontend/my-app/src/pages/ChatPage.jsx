import { useState, useRef } from "react";
import { LuMic, LuRotateCcw } from "react-icons/lu";
import { API_BASE } from "../config";
const API = API_BASE || "http://localhost:8000";
import { authFetch, ensureGuestId, getGuestId, apiErrorMessage } from "../auth";
import { registerLocalFile } from "../localFileStore";
import { getNotesMirrorRoot, mirrorUploadedBytes } from "../localDiskFolder";
import { sanitizeVirtualPath } from "../virtualPath";
import "../index.css";

export default function ChatPage({ onSignInClick }) {
  const makeSessionId = () =>
    (typeof crypto !== "undefined" && crypto.randomUUID
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`);
  const [prompt, setPrompt] = useState("");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState(null);
  const [listening, setListening] = useState(false);
  const [chatSessionId, setChatSessionId] = useState(makeSessionId);
  const fileInputRef = useRef(null);
  const recognitionRef = useRef(null);

  const handleUploadClick = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    e.target.value = "";
    setLoading(true);
    try {
      const { root: mirrorRoot } = await getNotesMirrorRoot();
      await ensureGuestId();
      if (!getGuestId()) {
        setMessage({ type: "error", text: "Could not connect to server. Is the backend running at http://localhost:8000?" });
        setLoading(false);
        return;
      }
      const formData = new FormData();
      formData.append("chat_session_id", chatSessionId);
      formData.append("file", file);
      const res = await authFetch(`${API}/chat/upload_ephemeral`, {
        method: "POST",
        body: formData,
      });
      let data = {};
      try {
        data = await res.json();
      } catch (_) {}
      const rel = sanitizeVirtualPath(data.path || file.name || "");
      if (rel && file) {
        registerLocalFile(rel, file);
        try {
          await mirrorUploadedBytes(file, rel, mirrorRoot);
        } catch (mirrorErr) {
          console.error("Local mirror write failed:", mirrorErr);
        }
      }
      if (res.ok) {
        setMessage({ type: "success", text: `Uploaded "${file.name}" for this chat session only.` });
      } else {
        setMessage({ type: "error", text: apiErrorMessage(data, res) });
      }
    } catch (err) {
      setMessage({
        type: "error",
        text: "Upload failed. Is the backend running? Start it with: uvicorn backend.api:app --host 0.0.0.0 --port 8000",
      });
    } finally {
      setLoading(false);
    }
  };

  const handleVoiceInput = () => {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      setMessage({ type: "error", text: "Voice input is not supported in this browser." });
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
        setPrompt((prev) => (prev ? `${prev} ${spoken}` : spoken));
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

  const handleSubmit = (e) => {
    e.preventDefault();
    const text = prompt.trim();
    if (!text) return;
    setMessage({ type: "info", text: "Sign in to query your notes with AI. Use the Sign in button above." });
    setPrompt("");
  };

  return (
    <div className="chat-page">
      <header className="chat-page-header">
        <div className="chat-page-brand">NoteScanner</div>
        <button type="button" className="auth-btn chat-page-signin" onClick={onSignInClick}>
          Sign in
        </button>
      </header>

      <main className="chat-page-main">
        <h1 className="chat-page-title">Where should we begin?</h1>

        <form className="chat-page-form" onSubmit={handleSubmit}>
          <div className="chat-page-input-wrap">
            <button
              type="button"
              className="chat-page-plus"
              onClick={handleUploadClick}
              title="Upload files or images"
              aria-label="Upload"
            >
              +
            </button>
            <input
              type="text"
              className="chat-page-input"
              placeholder="Ask anything"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              disabled={loading}
            />
            <button
              type="button"
              className={`chat-page-mic ${listening ? "chat-page-mic--active" : ""}`}
              onClick={handleVoiceInput}
              title={listening ? "Stop voice input" : "Voice input"}
              aria-label={listening ? "Stop voice input" : "Voice input"}
            >
              <LuMic size={18} />
            </button>
            <button
              type="button"
              className="chat-page-mic"
              onClick={async () => {
                const fd = new FormData();
                fd.append("chat_session_id", chatSessionId);
                await authFetch(`${API}/chat/session/clear`, { method: "POST", body: fd }).catch(() => {});
                setChatSessionId(makeSessionId());
                setMessage({ type: "info", text: "Chat cache cleared." });
              }}
              title="Refresh chat"
              aria-label="Refresh chat"
            >
              <LuRotateCcw size={18} />
            </button>
          </div>
          {message && (
            <p className={`chat-page-message chat-page-message--${message.type}`}>
              {message.text}
            </p>
          )}
        </form>

        <div className="chat-page-suggestions">
          <button type="button" className="chat-page-suggestion" onClick={() => setPrompt("Summarize my notes")}>
            Summarize my notes
          </button>
          <button type="button" className="chat-page-suggestion" onClick={() => setPrompt("Explain this concept")}>
            Explain this concept
          </button>
          <button type="button" className="chat-page-suggestion" onClick={() => setPrompt("Quiz me on my notes")}>
            Quiz me on my notes
          </button>
          <button type="button" className="chat-page-suggestion" onClick={() => setPrompt("Find key points")}>
            Find key points
          </button>
        </div>
      </main>

      <input
        ref={fileInputRef}
        type="file"
        accept="image/*,.pdf"
        onChange={handleFileChange}
        style={{ display: "none" }}
        aria-hidden
      />
    </div>
  );
}
