/**
 * Aligns with backend _strip_storage_path: virtual paths are course/file only,
 * never user_notes/ or legacy per-user id segments (so disk mirror stays under NoteScanner).
 */

function isUuidLike(seg) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(seg);
}

export function sanitizeVirtualPath(p) {
  if (p == null || p === "") return "";
  let parts = String(p)
    .replace(/\\/g, "/")
    .replace(/^\/+|\/+$/g, "")
    .split("/")
    .filter((x) => x && x !== ".");
  while (parts.length && parts[0].toLowerCase() === "user_notes") {
    parts.shift();
  }
  if (parts.length && /^guest_/i.test(parts[0])) parts.shift();
  else if (parts.length && isUuidLike(parts[0])) parts.shift();
  return parts.join("/");
}
