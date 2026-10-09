/**
 * Study AI mode. SDE-Prep shows this app inside its "Study AI" tab (`?embed=sde`). There the app
 * takes SDE-Prep's name and colours (embed.css) so the two read as one site. On GitHub Pages both
 * live on cherie-dips.github.io, so they also share the sign-in: SDE-Prep's "Ask AI" panel reads
 * the same session from localStorage.
 */
const params = new URLSearchParams(typeof window !== "undefined" ? window.location.search : "");

export const EMBEDDED = params.get("embed") === "sde";

/** The product name the student sees. */
export const APP_NAME = EMBEDDED ? "Study AI" : "NoteScanner";

/**
 * `?auth=login` / `?auth=register` opens that box on load (SDE-Prep's "Sign in" button).
 * Read once, then removed from the address so a reload doesn't open it again.
 */
export function takeAuthRequest() {
  const wanted = params.get("auth");
  if (!EMBEDDED || !wanted) return null;
  try {
    const url = new URL(window.location.href);
    url.searchParams.delete("auth");
    window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  } catch {
    /* ignore */
  }
  return wanted === "register" ? "register" : "login";
}

/** Messages from the SDE-Prep page around us, e.g. {type: "studyai:auth", view: "login"}. Same origin only. */
export function onHostMessage(handler) {
  if (!EMBEDDED) return () => {};
  const listener = (e) => {
    if (e.origin !== window.location.origin || !e.data || typeof e.data !== "object") return;
    handler(e.data);
  };
  window.addEventListener("message", listener);
  return () => window.removeEventListener("message", listener);
}

/**
 * Run `handler` each time SDE-Prep switches back to the Study AI tab, so data changed meanwhile
 * (e.g. a deck saved from Ask AI) shows up. Returns a cleanup function, for useEffect.
 */
export function onShown(handler) {
  return onHostMessage((msg) => {
    if (msg.type === "studyai:shown") handler();
  });
}
