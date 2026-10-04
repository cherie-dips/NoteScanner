import { useState } from "react";
import { API_BASE } from "../config";
import { setSessionId, markNewAccount, apiErrorMessage, errorText } from "../auth";
import "../index.css";

export default function Register({ onRegister, onSwitchToLogin, onClose, onShowPrivacy }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [acceptedPrivacy, setAcceptedPrivacy] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!acceptedPrivacy) {
      setError("Please read and accept the privacy note.");
      return;
    }
    setError("");
    setLoading(true);
    try {
      const formData = new FormData();
      formData.append("email", email);
      formData.append("password", password);
      formData.append("name", name);
      formData.append("accepted_privacy", "true");
      const url = `${API_BASE || "http://localhost:8000"}/register`;
      const res = await fetch(url, {
        method: "POST",
        body: formData,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(apiErrorMessage(data, res));
        return;
      }
      if (data.session_id) setSessionId(data.session_id, data.name ?? undefined, data.user_id ?? undefined);
      markNewAccount(data.user_id);
      onRegister?.();
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
        <p className="auth-subtitle">Create an account</p>
        <form onSubmit={handleSubmit} className="auth-form">
          <input
            type="email"
            placeholder="Email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="auth-input"
            required
            autoComplete="email"
          />
          <input
            type="text"
            placeholder="Name (optional)"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="auth-input"
            autoComplete="name"
          />
          <input
            type="password"
            placeholder="Password (at least 8 characters)"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="auth-input"
            required
            minLength={8}
            maxLength={72}
            autoComplete="new-password"
          />
          <label className="auth-consent">
            <input
              type="checkbox"
              className="auth-consent-checkbox"
              checked={acceptedPrivacy}
              onChange={(e) => setAcceptedPrivacy(e.target.checked)}
              required
            />
            <span>
              I've read the{" "}
              <button type="button" className="auth-link auth-privacy-link" onClick={onShowPrivacy}>
                privacy note
              </button>
            </span>
          </label>
          {error && <p className="auth-error">{error}</p>}
          <button type="submit" className="auth-btn" disabled={loading}>
            {loading ? "Creating account…" : "Sign up"}
          </button>
        </form>
        <p className="auth-switch">
          Already have an account?{" "}
          <button type="button" onClick={onSwitchToLogin} className="auth-link">
            Sign in
          </button>
        </p>
        {onClose && (
          <button type="button" className="auth-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        )}
      </div>
    </div>
  );
}
