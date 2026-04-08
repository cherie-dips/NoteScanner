/**
 * Maps virtual note paths (same strings as the server tree) to blob URLs for in-app preview.
 * Original PDFs/images stay on the device; nothing is fetched from the API as binaries.
 */

import { sanitizeVirtualPath } from "./virtualPath";

const _map = new Map();

function _norm(p) {
  return (p || "").replace(/\\/g, "/").replace(/^\/+/, "").replace(/\/+$/, "");
}

export function registerLocalFile(relPath, file) {
  const key = sanitizeVirtualPath(_norm(relPath));
  if (!key || !file) return;
  const prev = _map.get(key);
  if (prev?.url) URL.revokeObjectURL(prev.url);
  _map.set(key, { url: URL.createObjectURL(file), file });
}

export function getBlobUrlForPath(relPath) {
  const key = sanitizeVirtualPath(_norm(relPath));
  return _map.get(key)?.url ?? null;
}

export function forgetPath(relPath) {
  const key = sanitizeVirtualPath(_norm(relPath));
  const prev = _map.get(key);
  if (prev?.url) URL.revokeObjectURL(prev.url);
  _map.delete(key);
}

export function forgetPathPrefix(folderPrefix) {
  const prefix = sanitizeVirtualPath(_norm(folderPrefix));
  if (!prefix) return;
  const pfx = `${prefix}/`;
  for (const k of [..._map.keys()]) {
    if (k === prefix || k.startsWith(pfx)) forgetPath(k);
  }
}

/**
 * After a successful /move_path: re-key stored blobs from oldPath to newPath (file or whole folder).
 */
export function relocateLocalEntry(oldPath, newPath, isFolder) {
  const o = sanitizeVirtualPath(_norm(oldPath));
  const n = sanitizeVirtualPath(_norm(newPath));
  if (!o || !n) return;
  if (!isFolder) {
    const entry = _map.get(o);
    if (!entry) return;
    _map.delete(o);
    const prev = _map.get(n);
    if (prev?.url) URL.revokeObjectURL(prev.url);
    _map.set(n, entry);
    return;
  }
  const op = `${o}/`;
  const pairs = [];
  for (const k of _map.keys()) {
    if (k === o) pairs.push([k, n]);
    else if (k.startsWith(op)) pairs.push([k, `${n}/${k.slice(op.length)}`]);
  }
  pairs.sort((a, b) => b[0].length - a[0].length);
  for (const [from, to] of pairs) {
    const entry = _map.get(from);
    if (!entry) continue;
    _map.delete(from);
    const prev = _map.get(to);
    if (prev?.url) URL.revokeObjectURL(prev.url);
    _map.set(to, entry);
  }
}
