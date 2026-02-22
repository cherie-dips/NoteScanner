import { API_BASE } from "./config";

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

/** Build URL for file preview; append session_id or guest_id so backend can authorize. */
export function getFileUrl(path) {
  const safePath = path.split("/").map(encodeURIComponent).join("/");
  const base = `${_apiBase()}/user_notes/${safePath}`;
  const sessionId = getSessionId();
  if (sessionId) return `${base}?session_id=${encodeURIComponent(sessionId)}`;
  const guestId = getGuestId();
  if (guestId) return `${base}?guest_id=${encodeURIComponent(guestId)}`;
  return base;
}

export function authFetch(url, options = {}) {
  const headers = { ...getAuthHeadersForFetch(), ...options.headers };
  return fetch(url, { ...options, headers });
}

export function isSignedIn() {
  return !!getSessionId();
}
