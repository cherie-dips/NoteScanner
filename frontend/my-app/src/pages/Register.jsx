import { useState } from "react";
import { API_BASE } from "../config";
import { setSessionId } from "../auth";
import "../index.css";

export default function Register({ onRegister, onSwitchToLogin, onClose }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const formData = new FormData();
      formData.append("email", email);
      formData.append("password", password);
      formData.append("name", name);
      const url = `${API_BASE || "http://localhost:8000"}/register`;
      const res = await fetch(url, {
        method: "POST",
        body: formData,
      });
      let data = {};
      try {
        data = await res.json();
      } catch (_) {
        if (!res.ok) setError(res.statusText || "Registration failed.");
        return;
      }
      if (!res.ok) {
        setError(data.detail || data.error || "Registration failed.");
        return;
      }
      if (data.session_id) setSessionId(data.session_id, data.name ?? undefined);
      onRegister?.();
    } catch (err) {
      const msg = err?.message || "";
      setError(
        API_BASE
          ? "Network error. Is the backend running? Start it with: uvicorn backend.api:app --host 0.0.0.0 --port 8000"
          : "Network error. Set VITE_API_URL=http://localhost:8000 and ensure the backend is running."
      );
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
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="auth-input"
            required
            minLength={6}
            autoComplete="new-password"
          />
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
