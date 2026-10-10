import { useState, useRef } from "react";
import { LuMic } from "react-icons/lu";
import { EMBEDDED } from "../embed";
import "../index.css";

/** Signed-out landing page. Uploading and asking questions use paid AI services, so they need an account. */
export default function ChatPage({ onSignInClick, onShowPrivacy }) {
  const [prompt, setPrompt] = useState("");
  const [message, setMessage] = useState(null);
  const [listening, setListening] = useState(false);
  const recognitionRef = useRef(null);

  const askToSignIn = (text) => {
    setMessage({ type: "info", text });
    onSignInClick?.();
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
    if (!prompt.trim()) return;
    askToSignIn("Sign in to ask questions about your notes.");
  };

  return (
    <div className="chat-page">
      <header className="chat-page-header">
        {/* Inside Interview.ai the site header already names the page. */}
        <div className="chat-page-brand">{EMBEDDED ? "" : "NoteScanner"}</div>
        <button type="button" className="auth-btn chat-page-signin" onClick={onSignInClick}>
          Sign in
        </button>
      </header>

      <main className="chat-page-main">
        <h1 className="chat-page-title">{EMBEDDED ? "Study from your own notes" : "Where should we begin?"}</h1>
        {EMBEDDED && (
          <p className="chat-page-lede">
            Upload class notes, handouts or photos of handwritten pages. Ask questions about them and turn
            them into flashcards and quizzes. The same account works for Ask AI in Notes.
          </p>
        )}

        <form className="chat-page-form" onSubmit={handleSubmit}>
          <div className="chat-page-input-wrap">
            <button
              type="button"
              className="chat-page-plus"
              onClick={() => askToSignIn("Sign in to upload notes and chat with them.")}
              title="Sign in to upload files or images"
              aria-label="Upload (sign in required)"
            >
              +
            </button>
            <input
              type="text"
              className="chat-page-input"
              placeholder="Ask anything"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
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

      <footer className="chat-page-footer">
        <button type="button" className="chat-page-footer-link chat-page-privacy-link" onClick={onShowPrivacy}>
          Privacy
        </button>
      </footer>
    </div>
  );
}
