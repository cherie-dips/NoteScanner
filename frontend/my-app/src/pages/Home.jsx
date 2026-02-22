import { useState, useRef, useEffect } from "react";
import Explorer from "../components/Explorer";
import QueryInterface from "../components/QueryInterface";
import { getUserName, setUserName, authFetch } from "../auth";
import { API_BASE } from "../config";
import "../index.css";
import { HiOutlineUserCircle } from "react-icons/hi2";

function IconUserCircle({ size = 24 }) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" width={size} height={size} aria-hidden>
      <path fillRule="evenodd" d="M18.685 19.097A9.723 9.723 0 0 0 21.75 12c0-5.385-4.365-9.75-9.75-9.75S2.25 6.615 2.25 12a9.723 9.723 0 0 0 3.065 7.097A9.716 9.716 0 0 0 12 21.75a9.716 9.716 0 0 0 6.685-2.653Zm-12.54-1.285A7.486 7.486 0 0 1 12 15a7.486 7.486 0 0 1 5.855 2.812A8.224 8.224 0 0 1 12 20.25a8.224 8.224 0 0 1-5.855-2.438ZM15.75 9a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0Z" clipRule="evenodd" />
    </svg>
  );
}

export default function Home({ onLogout, onSignInClick, signedIn }) {
  const [preview, setPreview] = useState(null);
  const [profileOpen, setProfileOpen] = useState(false);
  const profileRef = useRef(null);

  useEffect(() => {
    const handleClickOutside = (e) => {
      if (profileRef.current && !profileRef.current.contains(e.target)) {
        setProfileOpen(false);
      }
    };
    document.addEventListener("click", handleClickOutside);
    return () => document.removeEventListener("click", handleClickOutside);
  }, []);

  useEffect(() => {
    if (!signedIn) return;
    if (getUserName()) return;
    authFetch(`${API_BASE}/me`)
      .then((res) => res.ok ? res.json() : null)
      .then((data) => { if (data?.name) setUserName(data.name); })
      .catch(() => {});
  }, [signedIn]);

  const handleFileSelect = (fileUrl) => {
    setPreview(fileUrl);
  };

  return (
    <div className="app-container">
      <div className="app-header">
        {signedIn ? (
          <div className="profile-wrap" ref={profileRef}>
            <button
              type="button"
              className="profile-trigger"
              onClick={() => setProfileOpen((o) => !o)}
              title="Profile"
              aria-label="Profile"
              aria-expanded={profileOpen}
              aria-haspopup="true"
            >
              <HiOutlineUserCircle size={24} />
            </button>
            {profileOpen && (
              <div className="profile-dropdown">
                <div className="profile-dropdown-name">{getUserName() || "User"}</div>
                <button type="button" className="profile-dropdown-signout" onClick={() => { setProfileOpen(false); onLogout(); }}>
                  Sign out
                </button>
              </div>
            )}
          </div>
        ) : (
          <button type="button" className="auth-btn" onClick={onSignInClick}>Sign in</button>
        )}
      </div>
      <div className="left-sidebar">
        <div className="left-sidebar-content">
          <Explorer onFileSelect={handleFileSelect} onDeletePath={() => setPreview(null)} />
        </div>
      </div>

      <div className="preview-pane">
        <div className="preview-content">
          {preview ? (
            (() => {
              const pathPart = preview.split("?")[0];
              const isPdf = pathPart.toLowerCase().endsWith(".pdf");
              return isPdf ? (
                <embed src={preview} type="application/pdf" />
              ) : (
                <img src={preview} alt="preview" />
              );
            })()
          ) : (
            <div className="preview-placeholder">
              <div className="preview-placeholder-icon">📄</div>
              <p>Select a file from the explorer to preview it here</p>
            </div>
          )}
        </div>
      </div>

      <div className="right-sidebar">
        <div className="right-sidebar-content">
          <QueryInterface />
        </div>
      </div>
    </div>
  );
}