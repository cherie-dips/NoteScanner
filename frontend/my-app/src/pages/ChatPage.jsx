import { useState, useRef } from "react";
import { API_BASE } from "../config";
const API = API_BASE || "http://localhost:8000";
import { authFetch, ensureGuestId, getGuestId } from "../auth";
import "../index.css";

export default function ChatPage({ onSignInClick }) {
  const [prompt, setPrompt] = useState("");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState(null);
  const fileInputRef = useRef(null);

  const handleUploadClick = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    e.target.value = "";
    setLoading(true);
    try {
      await ensureGuestId();
      if (!getGuestId()) {
        setMessage({ type: "error", text: "Could not connect to server. Is the backend running at http://localhost:8000?" });
        setLoading(false);
        return;
      }
      const formData = new FormData();
      formData.append("path", "");
      formData.append("file", file);
      formData.append("auto_extract", "true");
      const res = await authFetch(`${API}/upload_note`, {
        method: "POST",
        body: formData,
      });
      let data = {};
      try {
        data = await res.json();
      } catch (_) {}
      if (res.ok) {
        setMessage({ type: "success", text: `Uploaded "${file.name}". Sign in to organize notes and use the full explorer.` });
      } else {
        setMessage({ type: "error", text: data.detail || data.error || "Upload failed." });
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
