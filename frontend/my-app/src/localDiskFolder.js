/**
 * Mirror the virtual tree into storage on the device.
 *
 * Primary (Chrome / Edge): user creates a folder named NoteScanner on disk, then selects it in the
 * picker so the browser grants read/write access. The handle is stored in IndexedDB.
 *
 * Fallback: when showDirectoryPicker is unavailable, use OPFS (private browser storage).
 */

import { sanitizeVirtualPath } from "./virtualPath";
import { registerLocalFile, getBlobUrlForPath } from "./localFileStore";

/** Bump this string to wipe all prior folder handles + linked flags (one-time migration on load). */
const MIRROR_STORAGE_VERSION = "v20260407-reset";
const SCHEMA_KEY = "notescanner-mirror-schema";
const LEGACY_IDB_NAMES = ["notescanner-fs-handles"];

const IDB_NAME = `notescanner-fs-handles-${MIRROR_STORAGE_VERSION}`;
const IDB_STORE = "handles";
const IDB_KEY = "notesRoot";

/** Sync mirror of “we saved a directory handle” so UI can call showDirectoryPicker without awaiting IDB first (preserves user activation). */
const LS_DISK_ROOT_LINKED = `notescanner-root-linked-${MIRROR_STORAGE_VERSION}`;

/**
 * Delete old IndexedDB + localStorage from previous NoteScanner builds so folder linking starts clean.
 */
function migrateMirrorStorage() {
  if (typeof window === "undefined" || typeof indexedDB === "undefined") return;
  try {
    if (typeof localStorage === "undefined") return;
    const applied = localStorage.getItem(SCHEMA_KEY);
    if (applied === MIRROR_STORAGE_VERSION) return;

    const toDelete = new Set(LEGACY_IDB_NAMES);
    if (applied) {
      toDelete.add(`notescanner-fs-handles-${applied}`);
    }
    for (const name of toDelete) {
      try {
        indexedDB.deleteDatabase(name);
      } catch {
        /* ignore */
      }
    }
    if (applied) {
      try {
        localStorage.removeItem(`notescanner-root-linked-${applied}`);
      } catch {
        /* ignore */
      }
    }
    try {
      localStorage.removeItem("notescanner-notes-root-linked");
    } catch {
      /* ignore */
    }
    localStorage.setItem(SCHEMA_KEY, MIRROR_STORAGE_VERSION);
  } catch {
    /* ignore */
  }
}

migrateMirrorStorage();

function setDiskRootLinkedFlag(on) {
  try {
    if (typeof localStorage === "undefined") return;
    if (on) localStorage.setItem(LS_DISK_ROOT_LINKED, "1");
    else localStorage.removeItem(LS_DISK_ROOT_LINKED);
  } catch {
    /* private mode */
  }
}

/** Synchronous; use before showDirectoryPicker so no prior await breaks the user gesture. */
export function hasUserDiskRootLinkedSync() {
  try {
    return typeof localStorage !== "undefined" && localStorage.getItem(LS_DISK_ROOT_LINKED) === "1";
  } catch {
    return false;
  }
}

/** Shown in UI; directory name under OPFS or inside the user-picked parent. */
export const RECOMMENDED_ROOT_FOLDER_NAME = "NoteScanner";

function normRel(p) {
  return (p || "").replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
}

/** OPFS: no picker; create NoteScanner under the origin root. */
export function isOpfsMirrorSupported() {
  return typeof navigator !== "undefined" && typeof navigator.storage?.getDirectory === "function";
}

/** Optional visible folder on disk (Chrome, Edge, …). */
export function isUserFolderPickerSupported() {
  return typeof window !== "undefined" && typeof window.showDirectoryPicker === "function";
}

/** Any local mirroring (OPFS and/or picker). */
export function isLocalDiskMirrorSupported() {
  return isOpfsMirrorSupported() || isUserFolderPickerSupported();
}

function openDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(IDB_NAME, 1);
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains(IDB_STORE)) {
        req.result.createObjectStore(IDB_STORE);
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export async function loadStoredRootHandle() {
  try {
    const db = await openDb();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(IDB_STORE, "readonly");
      const r = tx.objectStore(IDB_STORE).get(IDB_KEY);
      r.onsuccess = () => {
        const val = r.result ?? null;
        if (val) setDiskRootLinkedFlag(true);
        resolve(val);
      };
      r.onerror = () => reject(r.error);
    });
  } catch {
    return null;
  }
}

export async function saveRootHandle(handle) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(IDB_STORE, "readwrite");
    tx.objectStore(IDB_STORE).put(handle, IDB_KEY);
    tx.oncomplete = () => {
      setDiskRootLinkedFlag(true);
      resolve();
    };
    tx.onerror = () => reject(tx.error);
  });
}

export async function clearStoredRootHandle() {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(IDB_STORE, "readwrite");
    tx.objectStore(IDB_STORE).delete(IDB_KEY);
    tx.oncomplete = () => {
      setDiskRootLinkedFlag(false);
      resolve();
    };
    tx.onerror = () => reject(tx.error);
  });
}

/**
 * Ask the browser for persistent storage (reduces eviction). No folder picker.
 * May show a permission prompt depending on browser and policy.
 */
export async function ensurePersistentStoragePermission() {
  if (!navigator.storage?.persist) return false;
  try {
    return await navigator.storage.persist();
  } catch {
    return false;
  }
}

/**
 * Ask for read/write on a user-picked directory handle (call from a gesture when possible).
 */
export async function requestRootPermission(handle) {
  if (!handle?.queryPermission) return "granted";
  let state = await handle.queryPermission({ mode: "readwrite" });
  if (state === "prompt" && handle.requestPermission) {
    state = await handle.requestPermission({ mode: "readwrite" });
  }
  return state;
}

async function getOpfsNoteScannerRoot() {
  await ensurePersistentStoragePermission();
  const opfs = await navigator.storage.getDirectory();
  return opfs.getDirectoryHandle(RECOMMENDED_ROOT_FOLDER_NAME, { create: true });
}

/**
 * Resolve the directory used for mirroring: linked user folder if permitted; else OPFS only when
 * the directory picker API is not available (otherwise callers must link or pick on first folder).
 */
export async function getNotesMirrorRoot() {
  const h = await loadStoredRootHandle();
  if (h) {
    const perm = await requestRootPermission(h);
    if (perm === "granted") {
      return { root: h, source: "user_folder" };
    }
  }
  if (!isUserFolderPickerSupported() && isOpfsMirrorSupported()) {
    try {
      const root = await getOpfsNoteScannerRoot();
      return { root, source: "opfs" };
    } catch {
      return { root: null, source: "none" };
    }
  }
  return { root: null, source: "none" };
}

/**
 * Guide the user to create a NoteScanner folder on disk, then pick that folder for read/write access.
 * @returns {Promise<FileSystemDirectoryHandle|null>} null if the user cancels or skips
 */
export async function linkNoteScannerFolder() {
  if (!isUserFolderPickerSupported()) {
    throw new Error(
      "This browser cannot pick a folder on disk. Local copies use private browser storage instead.",
    );
  }
  const ok = window.confirm(
    `Set up local files on your computer:\n\n` +
      `1. In Finder / File Explorer, create a new folder named "${RECOMMENDED_ROOT_FOLDER_NAME}" wherever you want (e.g. Documents or Desktop).\n\n` +
      `2. Click OK here, then in the next dialog select that "${RECOMMENDED_ROOT_FOLDER_NAME}" folder and allow access so NoteScanner can save your courses there.`,
  );
  if (!ok) return null;

  let handle;
  try {
    handle = await window.showDirectoryPicker({
      mode: "readwrite",
      // Browser limit: id must be ≤ 32 characters (Chromium).
      id: "notescanner-notes-root-pick",
    });
  } catch (e) {
    if (e?.name === "AbortError") return null;
    throw e;
  }

  if (handle.name !== RECOMMENDED_ROOT_FOLDER_NAME) {
    const proceed = window.confirm(
      `You selected “${handle.name}”. It should be named “${RECOMMENDED_ROOT_FOLDER_NAME}” so paths stay clear. Continue with this folder anyway?`,
    );
    if (!proceed) return null;
  }

  const perm = await requestRootPermission(handle);
  if (perm !== "granted") {
    throw new Error(
      "Read/write access to this folder was denied. Grant access so NoteScanner can save files there.",
    );
  }
  await saveRootHandle(handle);
  return handle;
}

export async function unlinkNoteScannerFolder() {
  await clearStoredRootHandle();
}

/**
 * Wipe folder-link storage (IndexedDB + flags). Use after a bad link or to redo first-folder setup.
 * Optional: `location.reload()` afterward so all tabs pick up a clean state.
 */
export async function flushLocalDiskMirrorData() {
  try {
    await new Promise((resolve) => {
      const req = indexedDB.deleteDatabase(IDB_NAME);
      req.onsuccess = req.onerror = req.onblocked = () => resolve();
    });
  } catch {
    /* ignore */
  }
  try {
    localStorage.removeItem(LS_DISK_ROOT_LINKED);
    localStorage.removeItem(SCHEMA_KEY);
    localStorage.removeItem("notescanner-notes-root-linked");
  } catch {
    /* ignore */
  }
}

async function walkDir(root, pathParts, create) {
  let dir = root;
  for (const part of pathParts) {
    if (!part) continue;
    dir = await dir.getDirectoryHandle(part, { create: !!create });
  }
  return dir;
}

/** Ensure directory chain exists for rel path (directories only, no file name). */
export async function mirrorEnsureDir(root, relDirPath) {
  const parts = sanitizeVirtualPath(normRel(relDirPath)).split("/").filter(Boolean);
  if (parts.length === 0) return root;
  return walkDir(root, parts, true);
}

export async function mirrorWriteFile(root, relPath, blobOrFile) {
  const n = sanitizeVirtualPath(normRel(relPath));
  if (!n) return;
  const parts = n.split("/");
  const name = parts.pop();
  const parent = await walkDir(root, parts, true);
  const fh = await parent.getFileHandle(name, { create: true });
  const w = await fh.createWritable();
  await w.write(blobOrFile);
  await w.close();
}

export async function mirrorRemove(root, relPath, isFolder) {
  const n = sanitizeVirtualPath(normRel(relPath));
  if (!n) return;
  const parts = n.split("/");
  const leaf = parts.pop();
  const parent = parts.length ? await walkDir(root, parts, false) : root;
  if (!parent) return;
  try {
    await parent.removeEntry(leaf, { recursive: !!isFolder });
  } catch {
    /* missing on disk is fine */
  }
}

async function getParentDir(root, relPath) {
  const parts = normRel(relPath).split("/").filter(Boolean);
  const leaf = parts.pop();
  const parent = parts.length ? await walkDir(root, parts, false) : root;
  return { parent, leaf };
}

export async function mirrorMoveFile(root, fromRel, toRel) {
  const from = sanitizeVirtualPath(normRel(fromRel));
  const to = sanitizeVirtualPath(normRel(toRel));
  if (!from || !to) return;
  const fromPn = await getParentDir(root, from);
  const toParentParts = normRel(to).split("/").filter(Boolean);
  const toName = toParentParts.pop();
  await walkDir(root, toParentParts, true);
  const toParent = toParentParts.length ? await walkDir(root, toParentParts, false) : root;
  try {
    const fh = await fromPn.parent.getFileHandle(fromPn.leaf);
    if (typeof fh.move === "function") {
      await fh.move(toParent, toName);
      return;
    }
  } catch {
    /* fall through */
  }
  try {
    const fh = await fromPn.parent.getFileHandle(fromPn.leaf);
    const file = await fh.getFile();
    await mirrorWriteFile(root, to, file);
    await fromPn.parent.removeEntry(fromPn.leaf);
  } catch {
    /* ignore */
  }
}

/**
 * Best-effort folder rename/move on disk (Chrome supports directory move in newer versions).
 */
export async function mirrorMoveFolder(root, fromRel, toRel) {
  const from = sanitizeVirtualPath(normRel(fromRel));
  const to = sanitizeVirtualPath(normRel(toRel));
  if (!from || !to) return;
  const fromPn = await getParentDir(root, from);
  const toParts = normRel(to).split("/").filter(Boolean);
  const newName = toParts.pop();
  await walkDir(root, toParts, true);
  const destParent = toParts.length ? await walkDir(root, toParts, false) : root;
  try {
    const dh = await fromPn.parent.getDirectoryHandle(fromPn.leaf);
    if (typeof dh.move === "function") {
      await dh.move(destParent, newName);
      return;
    }
  } catch {
    /* ignore */
  }
}

/**
 * Run fn(root) with the active mirror root (user-linked folder or OPFS), if any.
 */
export async function withNotesRoot(fn) {
  const { root } = await getNotesMirrorRoot();
  if (root) await fn(root);
}

/**
 * Write an uploaded file to the linked disk folder (or OPFS). Call {@link getNotesMirrorRoot}
 * once at the start of the file input handler (before long network awaits) so permission can
 * still be obtained while the user gesture is fresh.
 *
 * @param {File|Blob} file
 * @param {string} relPath virtual path from the server (e.g. CS/paper.pdf)
 * @param {FileSystemDirectoryHandle | null} [prefetchedRoot] from an earlier getNotesMirrorRoot()
 */
export async function mirrorUploadedBytes(file, relPath, prefetchedRoot = null) {
  const n = sanitizeVirtualPath(normRel(relPath));
  if (!n || !file) return;
  let root = prefetchedRoot;
  if (!root) {
    const got = await getNotesMirrorRoot();
    root = got.root;
  }
  if (!root) return;
  await mirrorWriteFile(root, n, file);
}

/**
 * Read a file from the linked NoteScanner folder (or OPFS mirror) and register it for in-app preview.
 * @param {string} relPath virtual path (e.g. CS/paper.pdf)
 * @param {FileSystemDirectoryHandle | null} [prefetchedRoot]
 * @returns {Promise<string|null>} blob URL, or null if missing / unreadable
 */
export async function hydrateLocalPreviewFromMirror(relPath, prefetchedRoot = null) {
  const n = sanitizeVirtualPath(normRel(relPath));
  if (!n) return null;
  let root = prefetchedRoot;
  if (!root) {
    const got = await getNotesMirrorRoot();
    root = got.root;
  }
  if (!root) return null;
  const parts = n.split("/").filter(Boolean);
  const name = parts.pop();
  if (!name) return null;
  try {
    const parent = await walkDir(root, parts, false);
    const fh = await parent.getFileHandle(name);
    const file = await fh.getFile();
    registerLocalFile(n, file);
    return getBlobUrlForPath(n);
  } catch {
    return null;
  }
}
