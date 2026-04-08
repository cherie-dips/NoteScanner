import { API_BASE } from "./config";
import { getBlobUrlForPath } from "./localFileStore";

const SESSION_KEY = "notescanner_session_id";
const GUEST_KEY = "notescanner_guest_id";
const USER_NAME_KEY = "notescanner_user_name";
const SESSION_HEADER = "X-Session-Id";
const GUEST_HEADER = "X-Guest-Id";

export function getSessionId() {
  return localStorage.getItem(SESSION_KEY);
}

export function setSessionId(sessionId, userName = null) {
  localStorage.setItem(SESSION_KEY, sessionId);
  localStorage.removeItem(GUEST_KEY);
  if (userName != null) localStorage.setItem(USER_NAME_KEY, userName);
}

export function removeSession() {
  localStorage.removeItem(SESSION_KEY);
  localStorage.removeItem(USER_NAME_KEY);
}

export function getUserName() {
  return localStorage.getItem(USER_NAME_KEY) || "";
}

export function setUserName(name) {
  if (name != null) localStorage.setItem(USER_NAME_KEY, name);
}

export function getGuestId() {
  return localStorage.getItem(GUEST_KEY);
}

export function setGuestId(guestId) {
  localStorage.setItem(GUEST_KEY, guestId);
}

const _apiBase = () => API_BASE || "http://localhost:8000";

/** Ensure we have a guest id (fetch from backend if missing). Returns a promise that resolves when ready. */
export async function ensureGuestId() {
  if (getSessionId()) return;
  let g = getGuestId();
  if (!g) {
    const res = await fetch(`${_apiBase()}/guest_id`);
    let data = {};
    try {
      data = await res.json();
    } catch (_) {}
    if (data.guest_id) {
      setGuestId(data.guest_id);
      g = data.guest_id;
    }
  }
  return g;
}

export function getAuthHeadersForFetch() {
  const sessionId = getSessionId();
  if (sessionId) return { [SESSION_HEADER]: sessionId };
  const guestId = getGuestId();
  if (guestId) return { [GUEST_HEADER]: guestId };
  return {};
}

/** Blob URL for a path if this device has a local copy (e.g. after upload). Server does not store binaries. */
export function getFileUrl(path) {
  return getBlobUrlForPath(path) || "";
}

export function authFetch(url, options = {}) {
  const headers = { ...getAuthHeadersForFetch(), ...options.headers };
  return fetch(url, { ...options, headers });
}

/** Readable string from FastAPI/JSON error bodies (detail, error, message). */
export function apiErrorMessage(data, res) {
  const status = res?.status;
  if (!data || typeof data !== "object") {
    return status ? `Request failed (HTTP ${status})` : "Request failed";
  }
  if (typeof data.detail === "string" && data.detail) return data.detail;
  if (Array.isArray(data.detail) && data.detail.length) {
    const parts = data.detail.map((x) =>
      x && typeof x === "object" && x.msg != null ? String(x.msg) : String(x),
    );
    const joined = parts.join("; ").trim();
    if (joined) return joined;
  }
  if (data.error) return String(data.error);
  if (data.message) return String(data.message);
  return status ? `Request failed (HTTP ${status})` : "Request failed";
}

export function isSignedIn() {
  return !!getSessionId();
}
