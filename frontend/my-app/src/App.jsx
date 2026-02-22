import { useState, useCallback, useEffect } from "react";
import { getSessionId, removeSession, ensureGuestId, isSignedIn } from "./auth";
import Login from "./pages/Login";
import Register from "./pages/Register";
import Home from "./pages/Home";
import ChatPage from "./pages/ChatPage";

function App() {
  const [signedIn, setSignedIn] = useState(isSignedIn());
  const [authView, setAuthView] = useState(null);

  useEffect(() => {
    if (!getSessionId()) ensureGuestId();
  }, []);

  const onLogin = useCallback(() => {
    setSignedIn(true);
    setAuthView(null);
  }, []);

  const onLogout = useCallback(() => {
    removeSession();
    setSignedIn(false);
    ensureGuestId();
  }, []);

  const openSignIn = useCallback(() => setAuthView("login"), []);
  const openSignUp = useCallback(() => setAuthView("register"), []);

  return (
    <>
      {signedIn ? (
        <Home
          onLogout={onLogout}
          onSignInClick={openSignIn}
          signedIn={signedIn}
        />
      ) : (
        <ChatPage onSignInClick={openSignIn} />
      )}
      {authView === "login" && (
        <div className="auth-overlay" onClick={() => setAuthView(null)}>
          <div className="auth-overlay-content" onClick={(e) => e.stopPropagation()}>
            <Login
              onLogin={onLogin}
              onSwitchToRegister={() => setAuthView("register")}
              onClose={() => setAuthView(null)}
            />
          </div>
        </div>
      )}
      {authView === "register" && (
        <div className="auth-overlay" onClick={() => setAuthView(null)}>
          <div className="auth-overlay-content" onClick={(e) => e.stopPropagation()}>
            <Register
              onRegister={onLogin}
              onSwitchToLogin={() => setAuthView("login")}
              onClose={() => setAuthView(null)}
            />
          </div>
        </div>
      )}
    </>
  );
}

export default App;
