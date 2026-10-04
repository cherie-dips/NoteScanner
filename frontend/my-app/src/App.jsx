import { useState, useCallback, useEffect } from "react";
import {
  getSessionId,
  removeSession,
  ensureGuestId,
  isSignedIn,
  signOutOnServer,
  SIGNED_OUT_EVENT,
} from "./auth";
import { DEFAULT_SERVER_CONFIG, loadServerConfig } from "./config";
import Login from "./pages/Login";
import Register from "./pages/Register";
import ResetPassword from "./pages/ResetPassword";
import Home from "./pages/Home";
import ChatPage from "./pages/ChatPage";
import PrivacyNote from "./components/PrivacyNote";

function readResetToken() {
  try {
    return new URLSearchParams(window.location.search).get("reset_token") || "";
  } catch {
    return "";
  }
}

/** Remove ?reset_token=… from the address bar without reloading. */
function clearResetTokenFromUrl() {
  try {
    const url = new URL(window.location.href);
    url.searchParams.delete("reset_token");
    window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  } catch {
    /* ignore */
  }
}

function App() {
  const [signedIn, setSignedIn] = useState(isSignedIn());
  const [resetToken, setResetToken] = useState(readResetToken);
  const [authView, setAuthView] = useState(() => (readResetToken() ? "reset" : null));
  const [loginNotice, setLoginNotice] = useState("");
  const [serverConfig, setServerConfig] = useState(DEFAULT_SERVER_CONFIG);
  const [privacyOpen, setPrivacyOpen] = useState(false);
  const openPrivacy = useCallback(() => setPrivacyOpen(true), []);
  const closePrivacy = useCallback(() => setPrivacyOpen(false), []);

  useEffect(() => {
    if (!getSessionId()) ensureGuestId();
  }, []);

  useEffect(() => {
    let cancelled = false;
    loadServerConfig().then((cfg) => {
      if (!cancelled) setServerConfig(cfg);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    // The server rejected our session (expired or signed out elsewhere).
    const handleSignedOut = () => {
      setSignedIn(false);
      setAuthView("login");
      ensureGuestId();
    };
    window.addEventListener(SIGNED_OUT_EVENT, handleSignedOut);
    return () => window.removeEventListener(SIGNED_OUT_EVENT, handleSignedOut);
  }, []);

  const onLogin = useCallback(() => {
    setSignedIn(true);
    setAuthView(null);
    setLoginNotice("");
  }, []);

  const onLogout = useCallback(() => {
    void signOutOnServer(); // reads the session id before removeSession() clears it
    removeSession();
    setSignedIn(false);
    ensureGuestId();
  }, []);

  const openSignIn = useCallback(() => setAuthView("login"), []);

  const closeAuth = useCallback(() => {
    if (authView === "reset") {
      clearResetTokenFromUrl();
      setResetToken("");
    }
    setAuthView(null);
  }, [authView]);

  useEffect(() => {
    if (!authView) return;
    const onKey = (e) => {
      if (e.key === "Escape") closeAuth();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [authView, closeAuth]);

  const onPasswordReset = useCallback((message) => {
    clearResetTokenFromUrl();
    setResetToken("");
    setLoginNotice(message);
    setAuthView("login");
  }, []);

  return (
    <>
      {signedIn ? (
        <Home
          onLogout={onLogout}
          onSignInClick={openSignIn}
          signedIn={signedIn}
          serverConfig={serverConfig}
          onShowPrivacy={openPrivacy}
        />
      ) : (
        <ChatPage onSignInClick={openSignIn} onShowPrivacy={openPrivacy} />
      )}
      {authView === "login" && (
        <div className="auth-overlay" onClick={closeAuth}>
          <div className="auth-overlay-content" onClick={(e) => e.stopPropagation()}>
            <Login
              onLogin={onLogin}
              onSwitchToRegister={() => setAuthView("register")}
              onClose={closeAuth}
              passwordResetEnabled={!!serverConfig.password_reset_enabled}
              notice={loginNotice}
            />
          </div>
        </div>
      )}
      {authView === "register" && (
        <div className="auth-overlay" onClick={closeAuth}>
          <div className="auth-overlay-content" onClick={(e) => e.stopPropagation()}>
            <Register
              onRegister={onLogin}
              onSwitchToLogin={() => setAuthView("login")}
              onClose={closeAuth}
              onShowPrivacy={openPrivacy}
            />
          </div>
        </div>
      )}
      {authView === "reset" && resetToken && (
        <div className="auth-overlay" onClick={closeAuth}>
          <div className="auth-overlay-content" onClick={(e) => e.stopPropagation()}>
            <ResetPassword token={resetToken} onDone={onPasswordReset} onClose={closeAuth} />
          </div>
        </div>
      )}
      {privacyOpen && <PrivacyNote onClose={closePrivacy} />}
    </>
  );
}

export default App;
