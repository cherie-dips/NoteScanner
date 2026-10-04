import { useEffect, useState } from "react";
import { apiRequest, errorText } from "../auth";

/** "Send feedback" from the profile menu: free text sent to POST /feedback (kind=general). */
export default function FeedbackModal({ onClose }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(null);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") onClose?.();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const send = async (e) => {
    e.preventDefault();
    const comment = text.trim();
    if (!comment) return;
    setBusy(true);
    setStatus(null);
    try {
      const data = await apiRequest("/feedback", {
        method: "POST",
        form: { kind: "general", rating: "", comment, question: "", answer: "", sources: "", selection_stage: "" },
      });
      setStatus({ type: "success", text: data.message || "Thanks for the feedback!" });
      setText("");
    } catch (err) {
      setStatus({ type: "error", text: errorText(err) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-card feedback-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="feedback-modal-title"
        onClick={(e) => e.stopPropagation()}
      >
        <button type="button" className="auth-close" onClick={onClose} aria-label="Close">
          ×
        </button>
        <h2 id="feedback-modal-title" className="modal-title">Send feedback</h2>
        <p className="settings-text">
          What's working, what's confusing, what would help you study? Every message is read.
        </p>
        <form className="auth-form" onSubmit={send}>
          <textarea
            className="auth-input feedback-text"
            rows={5}
            maxLength={4000}
            value={text}
            onChange={(e) => setText(e.target.value)}
            aria-label="Your feedback"
            placeholder="Your feedback"
            autoFocus
          />
          {status && <p className={`settings-status settings-status--${status.type} feedback-status`}>{status.text}</p>}
          <button type="submit" className="auth-btn feedback-send" disabled={busy || !text.trim()}>
            {busy ? "Sending…" : "Send"}
          </button>
        </form>
      </div>
    </div>
  );
}
