import { API_BASE } from "./config";
import { APP_NAME } from "./embed";
import { getBlobUrlForPath } from "./localFileStore";

/** localStorage key of the sign-in. SDE-Prep's "Ask AI" panel reads it too (same origin). */
export const SESSION_KEY = "notescanner_session_id";
const GUEST_KEY = "notescanner_guest_id";
const USER_NAME_KEY = "notescanner_user_name";
const USER_ID_KEY = "notescanner_user_id";
const SESSION_HEADER = "X-Session-Id";
const GUEST_HEADER = "X-Guest-Id";

/** Fired on window when the server rejects our session (expired or signed out elsewhere). */
export const SIGNED_OUT_EVENT = "notescanner:signed-out";

export const NETWORK_ERROR_MESSAGE =
  `Can't reach the ${APP_NAME} server. Please check your connection and try again.`;

export function getSessionId() {
  return localStorage.getItem(SESSION_KEY);
}

export function setSessionId(sessionId, userName = null, userId = null) {
  localStorage.setItem(SESSION_KEY, sessionId);
  localStorage.removeItem(GUEST_KEY);
  if (userName != null) localStorage.setItem(USER_NAME_KEY, userName);
  if (userId != null) localStorage.setItem(USER_ID_KEY, userId);
}

export function removeSession() {
  localStorage.removeItem(SESSION_KEY);
  localStorage.removeItem(USER_NAME_KEY);
  localStorage.removeItem(USER_ID_KEY);
}

/** Signed-in user's id (stored at sign-in), or "" if unknown. */
export function getUserId() {
  return localStorage.getItem(USER_ID_KEY) || "";
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
    const data = await res.json().catch(() => ({}));
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

/**
 * fetch() with the session/guest header. A 401 for our session signs the app out, unless
 * `keepSessionOn401` is set (for requests where 401 can mean "wrong password").
 */
export async function authFetch(url, options = {}) {
  const { keepSessionOn401 = false, ...fetchOptions } = options;
  const sessionId = getSessionId();
  const headers = { ...getAuthHeadersForFetch(), ...fetchOptions.headers };
  const res = await fetch(url, { ...fetchOptions, headers });
  if (res.status === 401 && !keepSessionOn401 && sessionId && getSessionId() === sessionId) {
    // Session expired or was ended: switch the app back to the signed-out view.
    removeSession();
    window.dispatchEvent(new Event(SIGNED_OUT_EVENT));
  }
  return res;
}

/** End the session on the server too, so the session id stops working everywhere. */
export async function signOutOnServer() {
  const sessionId = getSessionId();
  if (!sessionId) return;
  try {
    await fetch(`${_apiBase()}/logout`, {
      method: "POST",
      headers: { [SESSION_HEADER]: sessionId },
    });
  } catch {
    /* still signed out locally */
  }
}

/**
 * Call the API with the session header and return the parsed JSON body.
 * `form` (plain object) is sent as multipart form data. Throws Error(readable message) on failure.
 */
export async function apiRequest(path, { method = "GET", form = null, signal, keepSessionOn401 = false } = {}) {
  let body;
  if (form) {
    body = new FormData();
    for (const [key, value] of Object.entries(form)) body.append(key, value);
  }
  const res = await authFetch(`${API_BASE}${path}`, { method, body, signal, keepSessionOn401 });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(apiErrorMessage(data, res));
  return data;
}

/** Save a Blob as a file download. */
export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** User-facing text for a thrown error (fetch throws TypeError when the server can't be reached). */
export function errorText(err, fallback = "Something went wrong. Please try again.") {
  if (err instanceof TypeError) return NETWORK_ERROR_MESSAGE;
  return err?.message || fallback;
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

const NEW_ACCOUNT_KEY = "notescanner-new-account";

/** Remember that this user just signed up (the server adds starter notes a moment later). */
export function markNewAccount(userId) {
  try {
    if (userId) localStorage.setItem(NEW_ACCOUNT_KEY, userId);
  } catch {
    /* ignore */
  }
}

/** True if the signed-in user just signed up on this device (until clearNewAccountMark()). */
export function isNewAccount() {
  try {
    const uid = getUserId();
    return !!uid && localStorage.getItem(NEW_ACCOUNT_KEY) === uid;
  } catch {
    return false;
  }
}

export function clearNewAccountMark() {
  try {
    localStorage.removeItem(NEW_ACCOUNT_KEY);
  } catch {
    /* ignore */
  }
}

export function isSignedIn() {
  return !!getSessionId();
}
