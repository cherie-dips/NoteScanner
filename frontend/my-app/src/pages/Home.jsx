import { useState, useRef, useEffect, useCallback } from "react";
import Explorer from "../components/Explorer";
import QueryInterface from "../components/QueryInterface";
import StudyPanel from "../components/StudyPanel";
import { getUserName, setUserName, authFetch } from "../auth";
import { registerLocalFile, getBlobUrlForPath } from "../localFileStore";
import { hydrateLocalPreviewFromMirror } from "../localDiskFolder";
import { API_BASE } from "../config";
import "../index.css";
import { HiOutlineUserCircle } from "react-icons/hi2";

const LS_LEFT_W = "notescanner-left-sidebar-w";
const LS_RIGHT_W = "notescanner-right-sidebar-w";

const LEFT_W_MIN = 150;
const LEFT_W_MAX = 480;
const RIGHT_W_MIN = 200;
const RIGHT_W_MAX = 520;

function readLeftSidebarWidth() {
  try {
    const n = parseInt(localStorage.getItem(LS_LEFT_W) || "", 10);
    if (Number.isFinite(n)) return Math.min(LEFT_W_MAX, Math.max(LEFT_W_MIN, n));
  } catch {
    /* ignore */
  }
  return 250;
}

function readRightSidebarWidth() {
  try {
    const n = parseInt(localStorage.getItem(LS_RIGHT_W) || "", 10);
    if (Number.isFinite(n)) return Math.min(RIGHT_W_MAX, Math.max(RIGHT_W_MIN, n));
  } catch {
    /* ignore */
  }
  return 300;
}

export default function Home({ onLogout, onSignInClick, signedIn }) {
  const [preview, setPreview] = useState(null);
  const [previewHydrating, setPreviewHydrating] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [leftSidebarPx, setLeftSidebarPx] = useState(readLeftSidebarWidth);
  const [rightSidebarPx, setRightSidebarPx] = useState(readRightSidebarWidth);
  const profileRef = useRef(null);
  const leftSidebarRef = useRef(null);
  const rightSidebarRef = useRef(null);
  const previewAttachRef = useRef(null);

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
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data?.name) setUserName(data.name);
      })
      .catch(() => {});
  }, [signedIn]);

  const handleFileSelect = useCallback((fileUrl, name, path) => {
    const url = fileUrl || getBlobUrlForPath(path) || null;
    setPreview({ url, name, path });
  }, []);

  useEffect(() => {
    if (!preview?.path || preview?.url) return;
    const pathToLoad = preview.path;
    let cancelled = false;
    setPreviewHydrating(true);
    (async () => {
      try {
        const url = await hydrateLocalPreviewFromMirror(pathToLoad);
        if (cancelled) return;
        if (url) {
          setPreview((p) => (p?.path === pathToLoad ? { ...p, url } : p));
        }
      } finally {
        if (!cancelled) setPreviewHydrating(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [preview?.path, preview?.url]);

  const handlePreviewAttach = (e) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file || !preview?.path) return;
    registerLocalFile(preview.path, file);
    setPreview((p) =>
      p ? { ...p, url: getBlobUrlForPath(p.path), name: file.name || p.name } : null,
    );
  };

  /** Width updates go straight to the DOM during drag; React state commits on pointer up (smooth, no child re-renders per frame). */
  const handleLeftResizerPointerDown = (e) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    e.preventDefault();
    const handle = e.currentTarget;
    handle.setPointerCapture(e.pointerId);
    handle.classList.add("pane-resizer--active");

    const startX = e.clientX;
    const pane = leftSidebarRef.current;
    const startW = pane?.getBoundingClientRect().width ?? leftSidebarPx;

    const applyWidth = (w) => {
      const clamped = Math.min(LEFT_W_MAX, Math.max(LEFT_W_MIN, w));
      if (pane) pane.style.width = `${clamped}px`;
      return clamped;
    };

    const onMove = (ev) => {
      if (ev.pointerId !== e.pointerId) return;
      const dx = ev.clientX - startX;
      applyWidth(startW + dx);
    };

    const finish = (ev) => {
      if (ev.pointerId !== e.pointerId) return;
      try {
        handle.releasePointerCapture(e.pointerId);
      } catch {
        /* ignore */
      }
      handle.classList.remove("pane-resizer--active");
      handle.removeEventListener("pointermove", onMove);
      handle.removeEventListener("pointerup", finish);
      handle.removeEventListener("pointercancel", finish);
      document.body.style.removeProperty("cursor");
      document.body.style.removeProperty("user-select");

      const w = Math.round(
        pane?.getBoundingClientRect().width ??
          Math.min(LEFT_W_MAX, Math.max(LEFT_W_MIN, startW)),
      );
      const clamped = Math.min(LEFT_W_MAX, Math.max(LEFT_W_MIN, w));
      setLeftSidebarPx(clamped);
      if (pane) pane.style.width = `${clamped}px`;
      try {
        localStorage.setItem(LS_LEFT_W, String(clamped));
      } catch {
        /* ignore */
      }
    };

    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    handle.addEventListener("pointermove", onMove);
    handle.addEventListener("pointerup", finish);
    handle.addEventListener("pointercancel", finish);
  };

  const handleRightResizerPointerDown = (e) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    e.preventDefault();
    const handle = e.currentTarget;
    handle.setPointerCapture(e.pointerId);
    handle.classList.add("pane-resizer--active");

    const startX = e.clientX;
    const pane = rightSidebarRef.current;
    const startW = pane?.getBoundingClientRect().width ?? rightSidebarPx;

    const applyWidth = (w) => {
      const clamped = Math.min(RIGHT_W_MAX, Math.max(RIGHT_W_MIN, w));
      if (pane) pane.style.width = `${clamped}px`;
      return clamped;
    };

    const onMove = (ev) => {
      if (ev.pointerId !== e.pointerId) return;
      const dx = startX - ev.clientX;
      applyWidth(startW + dx);
    };

    const finish = (ev) => {
      if (ev.pointerId !== e.pointerId) return;
      try {
        handle.releasePointerCapture(e.pointerId);
      } catch {
        /* ignore */
      }
      handle.classList.remove("pane-resizer--active");
      handle.removeEventListener("pointermove", onMove);
      handle.removeEventListener("pointerup", finish);
      handle.removeEventListener("pointercancel", finish);
      document.body.style.removeProperty("cursor");
      document.body.style.removeProperty("user-select");

      const w = Math.round(
        pane?.getBoundingClientRect().width ??
          Math.min(RIGHT_W_MAX, Math.max(RIGHT_W_MIN, startW)),
      );
      const clamped = Math.min(RIGHT_W_MAX, Math.max(RIGHT_W_MIN, w));
      setRightSidebarPx(clamped);
      if (pane) pane.style.width = `${clamped}px`;
      try {
        localStorage.setItem(LS_RIGHT_W, String(clamped));
      } catch {
        /* ignore */
      }
    };

    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    handle.addEventListener("pointermove", onMove);
    handle.addEventListener("pointerup", finish);
    handle.addEventListener("pointercancel", finish);
  };

  const isPdf = preview?.name?.toLowerCase().endsWith(".pdf");
  const activeFilePath = preview?.path || "";
  const activeCourseFolder = activeFilePath.includes("/")
    ? activeFilePath.split("/")[0]
    : activeFilePath;

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
                <button
                  type="button"
                  className="profile-dropdown-signout"
                  onClick={() => {
                    setProfileOpen(false);
                    onLogout();
                  }}
                >
                  Sign out
                </button>
              </div>
            )}
          </div>
        ) : (
          <button type="button" className="auth-btn" onClick={onSignInClick}>
            Sign in
          </button>
        )}
      </div>

      <div className="app-main-row">
        <div
          ref={leftSidebarRef}
          className="left-sidebar"
          style={{ width: leftSidebarPx }}
        >
          <div className="left-sidebar-content">
            <Explorer
              onFileSelect={handleFileSelect}
              onDeletePath={(path, isFolder) => {
                if (
                  preview?.path &&
                  (preview.path === path ||
                    (isFolder && preview.path.startsWith(`${path}/`)))
                ) {
                  setPreview(null);
                }
              }}
              onMovePath={(from, to, isFolder) => {
                if (!preview?.path || !to) return;
                if (!isFolder && preview.path === from) {
                  setPreview((p) => (p ? { ...p, path: to } : null));
                  return;
                }
                if (isFolder && preview.path.startsWith(`${from}/`)) {
                  setPreview((p) =>
                    p
                      ? {
                          ...p,
                          path: `${to}/${p.path.slice(from.length + 1)}`,
                        }
                      : null,
                  );
                }
              }}
            />
          </div>
        </div>

        <div
          className="pane-resizer pane-resizer--vertical"
          onPointerDown={handleLeftResizerPointerDown}
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize file explorer and preview"
          title="Drag to resize"
        />

        <div className="preview-pane">
          <div className="preview-content">
            {preview?.url ? (
              isPdf ? (
                <embed
                  src={preview.url}
                  type="application/pdf"
                  title={preview.name || "PDF"}
                />
              ) : (
                <img src={preview.url} alt="preview" />
              )
            ) : previewHydrating ? (
              <div className="preview-placeholder">
                <div className="preview-placeholder-icon">📄</div>
                <p>Opening from your NoteScanner folder…</p>
              </div>
            ) : preview?.path ? (
              <div className="preview-placeholder">
                <div className="preview-placeholder-icon">📄</div>
                <p>
                  No copy of this file was found in your linked NoteScanner folder. Upload it again
                  or choose the file below (metadata and search stay in the cloud database).
                </p>
                <input
                  ref={previewAttachRef}
                  type="file"
                  accept="image/*,.pdf,application/pdf"
                  className="preview-attach-input"
                  aria-hidden
                  tabIndex={-1}
                  onChange={handlePreviewAttach}
                />
                <button
                  type="button"
                  className="preview-attach-btn"
                  onClick={() => previewAttachRef.current?.click()}
                >
                  Choose file…
                </button>
              </div>
            ) : (
              <div className="preview-placeholder">
                <div className="preview-placeholder-icon">📄</div>
                <p>Select a file from the explorer to preview it here</p>
              </div>
            )}
          </div>
        </div>

        <div
          className="pane-resizer pane-resizer--vertical"
          onPointerDown={handleRightResizerPointerDown}
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize preview and chat panels"
          title="Drag to resize"
        />

        <div
          ref={rightSidebarRef}
          className="right-sidebar"
          style={{ width: rightSidebarPx }}
        >
          <div className="right-sidebar-content">
            <QueryInterface
              activeFilePath={activeFilePath}
              activeCourseFolder={activeCourseFolder}
            />
            <StudyPanel
              activeFilePath={activeFilePath}
              activeCourseFolder={activeCourseFolder}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
