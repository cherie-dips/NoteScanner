/**
 * API base URL for the NoteScanner backend.
 * - In dev (npm run dev): defaults to http://localhost:8000
 * - In production: set VITE_API_URL when building (e.g. in GitHub Actions)
 *   If not set, defaults to same origin (for when frontend and backend are served together).
 */
export const API_BASE =
  import.meta.env.VITE_API_URL ||
  (import.meta.env.DEV ? "http://localhost:8000" : "");
