/** Deck name for a file: its name without the extension ("Physics/Week 1.pdf" → "Week 1"). */
export function defaultDeckName(path) {
  const base = (path || "").split("/").pop() || "Notes";
  return base.replace(/\.[^.]+$/, "") || base;
}

/** True if a stored path looks like a file (has an extension), false for a course folder. */
export function looksLikeFile(path) {
  return /\.[a-z0-9]{1,8}$/i.test((path || "").split("/").pop() || "");
}

/** Fisher–Yates shuffle (returns a new array). */
export function shuffled(list) {
  const out = [...list];
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}
