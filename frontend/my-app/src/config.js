/**
 * API base URL for the NoteScanner backend.
 * - VITE_API_URL (if set) wins.
 * - In dev or when opened from localhost: http://localhost:8000.
 * - In production (non-localhost): same origin unless VITE_API_URL is set.
 */
function getDefaultApiBase() {
  if (import.meta.env.VITE_API_URL) return import.meta.env.VITE_API_URL;
  if (import.meta.env.DEV) return "http://localhost:8000";
  if (typeof window !== "undefined" && window.location?.hostname === "localhost") return "http://localhost:8000";
  return "";
}
// Use getter so that when bundle runs in browser (e.g. localhost), we get the right default
export const API_BASE = typeof window !== "undefined" ? getDefaultApiBase() : (import.meta.env.VITE_API_URL || "");
