import { useState } from "react";
import { API_BASE } from "../config";
import { setSessionId, apiErrorMessage, errorText } from "../auth";
import { APP_NAME, EMBEDDED } from "../embed";
import "../index.css";

export default function Login({
  onLogin,
  onSwitchToRegister,
  onClose,
  passwordResetEnabled = false,
  notice = "",
}) {
  const [mode, setMode] = useState("login"); // login | forgot
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const formData = new FormData();
      formData.append("email", email);
      formData.append("password", password);
      const res = await fetch(`${API_BASE}/login`, {
        method: "POST",
        body: formData,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(apiErrorMessage(data, res));
        return;
      }
      if (data.session_id) setSessionId(data.session_id, data.name ?? undefined, data.user_id ?? undefined);
      onLogin?.();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  };

  const handleForgot = async (e) => {
    e.preventDefault();
    setError("");
    setInfo("");
    setLoading(true);
    try {
      const formData = new FormData();
      formData.append("email", email);
      const res = await fetch(`${API_BASE}/password/forgot`, { method: "POST", body: formData });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(apiErrorMessage(data, res));
        return;
      }
      setInfo(data.message || "If an account exists for that email, we sent a link to reset the password.");
    } catch (err) {
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  };

  const switchMode = (next) => {
    setMode(next);
    setError("");
    setInfo("");
  };

  return (
    <div className="auth-page">
      <div className="auth-card">
        <h1 className="auth-title">{APP_NAME}</h1>
        {mode === "forgot" ? (
          <>
            <p className="auth-subtitle">Reset your password</p>
            <form onSubmit={handleForgot} className="auth-form auth-forgot-form">
              <input
                type="email"
                placeholder="Email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="auth-input"
                required
                autoComplete="email"
              />
              {error && <p className="auth-error">{error}</p>}
              {info && <p className="auth-info">{info}</p>}
              <button type="submit" className="auth-btn" disabled={loading}>
                {loading ? "Sending…" : "Send reset link"}
              </button>
            </form>
            <p className="auth-switch">
              <button type="button" onClick={() => switchMode("login")} className="auth-link">
                Back to sign in
              </button>
            </p>
          </>
        ) : (
          <>
            <p className="auth-subtitle">
              {EMBEDDED ? "One account for Study AI and Ask AI" : "Sign in to your account"}
            </p>
            {notice && <p className="auth-info auth-notice">{notice}</p>}
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
                type="password"
                placeholder="Password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="auth-input"
                required
                autoComplete="current-password"
              />
              {error && <p className="auth-error">{error}</p>}
              <button type="submit" className="auth-btn" disabled={loading}>
                {loading ? "Signing in…" : "Sign in"}
              </button>
            </form>
            {passwordResetEnabled && (
              <p className="auth-forgot">
                <button type="button" onClick={() => switchMode("forgot")} className="auth-link auth-forgot-link">
                  Forgot password?
                </button>
              </p>
            )}
            <p className="auth-switch">
              Don’t have an account?{" "}
              <button type="button" onClick={onSwitchToRegister} className="auth-link">
                Sign up
              </button>
            </p>
          </>
        )}
        {onClose && (
          <button type="button" className="auth-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        )}
      </div>
    </div>
  );
}
