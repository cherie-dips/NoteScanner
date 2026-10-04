import { useState } from "react";
import { API_BASE } from "../config";
import { apiErrorMessage, errorText } from "../auth";
import "../index.css";

const MIN_PASSWORD = 8;
const MAX_PASSWORD = 72;

/** "Set a new password" form opened from the emailed link (?reset_token=…). */
export default function ResetPassword({ token, onDone, onClose }) {
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");
    if (password.length < MIN_PASSWORD || password.length > MAX_PASSWORD) {
      setError(`Password must be ${MIN_PASSWORD}–${MAX_PASSWORD} characters.`);
      return;
    }
    if (password !== confirm) {
      setError("The passwords don't match.");
      return;
    }
    setLoading(true);
    try {
      const formData = new FormData();
      formData.append("token", token);
      formData.append("new_password", password);
      const res = await fetch(`${API_BASE}/password/reset`, { method: "POST", body: formData });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(apiErrorMessage(data, res));
        return;
      }
      onDone?.(data.message || "Your password was changed. Sign in with your new password.");
    } catch (err) {
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="auth-page">
      <div className="auth-card">
        <h1 className="auth-title">NoteScanner</h1>
        <p className="auth-subtitle">Set a new password</p>
        <form onSubmit={handleSubmit} className="auth-form auth-reset-form">
          <input
            type="password"
            placeholder={`New password (at least ${MIN_PASSWORD} characters)`}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="auth-input"
            required
            minLength={MIN_PASSWORD}
            maxLength={MAX_PASSWORD}
            autoComplete="new-password"
            aria-label="New password"
          />
          <input
            type="password"
            placeholder="Repeat new password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            className="auth-input"
            required
            autoComplete="new-password"
            aria-label="Repeat new password"
          />
          {error && <p className="auth-error">{error}</p>}
          <button type="submit" className="auth-btn" disabled={loading}>
            {loading ? "Saving…" : "Set new password"}
          </button>
        </form>
        {onClose && (
          <button type="button" className="auth-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        )}
      </div>
    </div>
  );
}
