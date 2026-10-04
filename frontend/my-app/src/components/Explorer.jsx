import { useEffect, useState, useRef, useCallback } from "react";
import {
  VscNewFile,
  VscNewFolder,
  VscRefresh,
  VscCollapseAll,
  VscFolderOpened,
} from "react-icons/vsc";
import { BsFileEarmarkPdf } from "react-icons/bs";
import FileUpload from "./FileUpload";
import UploadProgress from "./UploadProgress";
import useUploadQueue from "./useUploadQueue";
import { API_BASE } from "../config";
import { authFetch, getFileUrl, ensureGuestId, apiErrorMessage, errorText } from "../auth";
import {
  forgetPath,
  forgetPathPrefix,
  relocateLocalEntry,
} from "../localFileStore";
import {
  isOpfsMirrorSupported,
  isUserFolderPickerSupported,
  loadStoredRootHandle,
  linkNoteScannerFolder,
  ensurePersistentStoragePermission,
  withNotesRoot,
  mirrorEnsureDir,
  mirrorWriteFile,
  mirrorRemove,
  mirrorMoveFile,
  mirrorMoveFolder,
} from "../localDiskFolder";
import { sanitizeVirtualPath } from "../virtualPath";
import { makeDeckFromFile } from "../studyApi";

const LS_AUTO_FLASHCARDS = "notescanner-auto-flashcards";

function readAutoFlashcards() {
  try {
    return localStorage.getItem(LS_AUTO_FLASHCARDS) === "1";
  } catch {
    return false;
  }
}

// ─── Icons ───────────────────────────────────────────────────────────────────

function FileTypeIcon({ name, size = 16 }) {
  const ext = name.includes(".") ? name.split(".").pop().toLowerCase() : "";
  const isPdf = ext === "pdf";
  const isImage = ["png","jpg","jpeg","gif","webp","bmp","tiff","svg"].includes(ext);
  const isText = ["txt","md","json","xml","csv","log"].includes(ext);

  if (isPdf) return (
    <span className="vsc2-file-icon vsc2-icon-pdf"><BsFileEarmarkPdf size={size} /></span>
  );
  if (isImage) return (
    <span className="vsc2-file-icon vsc2-icon-image">
      <svg viewBox="0 0 16 16" width={size} height={size} fill="currentColor">
        <path d="M14 2H2c-.55 0-1 .45-1 1v10c0 .55.45 1 1 1h12c.55 0 1-.45 1-1V3c0-.55-.45-1-1-1zm0 11H2V3h12v10zM4 10l2-2 2 2 2-3 3 4H4z"/>
      </svg>
    </span>
  );
  if (isText) return (
    <span className="vsc2-file-icon vsc2-icon-text">
      <svg viewBox="0 0 16 16" width={size} height={size} fill="currentColor">
        <path d="M3 2h10a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1zm1 2v1h8V4H4zm0 3v1h8V7H4zm0 3v1h5v-1H4z"/>
      </svg>
    </span>
  );
  return (
    <span className="vsc2-file-icon vsc2-icon-default">
      <svg viewBox="0 0 16 16" width={size} height={size} fill="currentColor">
        <path d="M10 1H3c-.55 0-1 .45-1 1v12c0 .55.45 1 1 1h10c.55 0 1-.45 1-1V5L10 1zm1 12H3V2h6v4h4v7z"/>
      </svg>
    </span>
  );
}

/** Rewrite a path (or anything under it) after `from` was moved/renamed to `to`. */
function remapPath(p, from, to) {
  if (p === from) return to;
  if (p && p.startsWith(`${from}/`)) return `${to}/${p.slice(from.length + 1)}`;
  return p;
}

// ─── Main Component ───────────────────────────────────────────────────────────

export default function Explorer({
  onFileSelect,
  onDeletePath,
  onMovePath,
  onTreeChange,
  activePath = "",
  refreshKey = 0,
  maxUploadMb = null,
}) {
  const [tree, setTree] = useState([]);
  const [fileMeta, setFileMeta] = useState({});
  const [expandedFolders, setExpandedFolders] = useState(new Set());
  const [selectedItem, setSelectedItem] = useState(null);
  const [selectedIsFolder, setSelectedIsFolder] = useState(false);
  /** 'folder' | 'file' | 'rename' | null */
  const [createInputMode, setCreateInputMode] = useState(null);
  const [createInputValue, setCreateInputValue] = useState("");
  const [renameNode, setRenameNode] = useState(null);
  const [dragSource, setDragSource] = useState(null);
  const [dropTarget, setDropTarget] = useState(null);
  const [externalDragOver, setExternalDragOver] = useState(false);
  const [newFileMenuOpen, setNewFileMenuOpen] = useState(false);
  const [ctxMenu, setCtxMenu] = useState(null);
  const [treeError, setTreeError] = useState("");
  const fileInputRef = useRef(null);
  const createInputRef = useRef(null);
  const createRowRef = useRef(null);
  const newFileMenuWrapRef = useRef(null);

  useEffect(() => {
    if (!isUserFolderPickerSupported() && isOpfsMirrorSupported()) {
      void ensurePersistentStoragePermission();
    }
  }, []);

  /** Restore localStorage linked-flag from IndexedDB (e.g. after deploy) so we don’t show the picker again. */
  useEffect(() => {
    void loadStoredRootHandle();
  }, []);

  const fetchFileMeta = useCallback(async () => {
    try {
      const res = await authFetch(`${API_BASE}/file_meta`);
      if (!res.ok) return;
      const data = await res.json().catch(() => ({}));
      setFileMeta(data.meta && typeof data.meta === "object" ? data.meta : {});
    } catch {
      /* stars are optional */
    }
  }, []);

  const fetchTree = useCallback(async () => {
    try {
      const res = await authFetch(`${API_BASE}/list_tree`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(apiErrorMessage(data, res));
      setTree(data.tree || []);
      setTreeError("");
      onTreeChange?.();
      void fetchFileMeta();
    } catch (err) {
      setTreeError(`Couldn't load your files. ${errorText(err)}`);
    }
  }, [onTreeChange, fetchFileMeta]);

  useEffect(() => {
    ensureGuestId()
      .catch(() => {})
      .then(() => fetchTree());
  }, [fetchTree, refreshKey]);

  // Finished uploads refresh the tree and, if switched on, become a flashcard deck right away.
  const [autoFlashcards, setAutoFlashcards] = useState(readAutoFlashcards);
  const jobDoneRef = useRef(null);
  const onJobDone = useCallback((job, key) => jobDoneRef.current?.(job, key), []);
  const uploads = useUploadQueue({ maxUploadMb, onJobDone });
  const { updateItem: updateUploadItem } = uploads;

  const makeFlashcards = useCallback(
    async (key, path) => {
      if (!path) return;
      updateUploadItem(key, { deckStatus: "making", deckMessage: "" });
      try {
        const deck = await makeDeckFromFile(path);
        updateUploadItem(key, {
          deckStatus: "done",
          deckMessage: `Deck ‘${deck.name}’ saved (${deck.count} cards). Review it in Study → Review.`,
        });
      } catch (err) {
        updateUploadItem(key, { deckStatus: "error", deckMessage: errorText(err) });
      }
    },
    [updateUploadItem],
  );

  useEffect(() => {
    jobDoneRef.current = (job, key) => {
      void fetchTree();
      if (autoFlashcards && key) void makeFlashcards(key, job?.path);
    };
  }, [fetchTree, makeFlashcards, autoFlashcards]);

  const toggleAutoFlashcards = (on) => {
    setAutoFlashcards(on);
    try {
      localStorage.setItem(LS_AUTO_FLASHCARDS, on ? "1" : "0");
    } catch {
      /* ignore */
    }
  };

  // Follow the file opened elsewhere (e.g. a chat source): select it and open its folders.
  useEffect(() => {
    if (!activePath) return;
    setSelectedItem(activePath);
    setSelectedIsFolder(false);
    const parts = activePath.split("/");
    if (parts.length < 2) return;
    setExpandedFolders((prev) => {
      const next = new Set(prev);
      for (let i = 1; i < parts.length; i++) next.add(parts.slice(0, i).join("/"));
      return next;
    });
  }, [activePath]);

  const closeCreateInput = () => {
    setCreateInputMode(null);
    setCreateInputValue("");
    setRenameNode(null);
  };

  useEffect(() => {
    if (!createInputMode) return;
    const handleClickOutside = (e) => {
      if (createRowRef.current && !createRowRef.current.contains(e.target)) {
        setCreateInputMode(null);
        setCreateInputValue("");
        setRenameNode(null);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [createInputMode]);

  useEffect(() => {
    if (!newFileMenuOpen) return;
    const close = (e) => {
      if (newFileMenuWrapRef.current && !newFileMenuWrapRef.current.contains(e.target)) {
        setNewFileMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [newFileMenuOpen]);

  useEffect(() => {
    if (!ctxMenu) return;
    const close = (e) => {
      if (e?.target?.closest?.(".vsc2-context-menu")) return;
      setCtxMenu(null);
    };
    const onKey = (e) => {
      if (e.key === "Escape") setCtxMenu(null);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", onKey);
    };
  }, [ctxMenu]);

  const openContextMenu = (e, node) => {
    e.preventDefault();
    e.stopPropagation();
    setCtxMenu({ x: e.clientX, y: e.clientY, node });
  };

  const copyNodePath = async (node) => {
    const p = node.path.replace(/\\/g, "/");
    try {
      await navigator.clipboard.writeText(p);
    } catch {
      try {
        const ta = document.createElement("textarea");
        ta.value = p;
        ta.style.position = "fixed";
        ta.style.left = "-9999px";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      } catch {
        alert("Could not copy path.");
      }
    }
    setCtxMenu(null);
  };

  const clearTreeSelection = () => {
    setSelectedItem(null);
    setSelectedIsFolder(false);
  };

  const handleTreeMouseDown = (e) => {
    if (e.target.closest(".vsc2-row")) return;
    clearTreeSelection();
  };

  const toggleFolder = (folderPath) => {
    setExpandedFolders((prev) => {
      const next = new Set(prev);
      next.has(folderPath) ? next.delete(folderPath) : next.add(folderPath);
      return next;
    });
  };

  const parentPathForCreate = selectedIsFolder && selectedItem
    ? selectedItem
    : selectedItem && !selectedIsFolder
      ? selectedItem.replace(/\/[^/]+$/, "")
      : "";

  const openCreateFolderInput = () => {
    setRenameNode(null);
    setCreateInputValue("");
    setCreateInputMode("folder");
    setTimeout(() => createInputRef.current?.focus(), 0);
  };

  const openCreateFileInput = () => {
    setRenameNode(null);
    setCreateInputValue("");
    setCreateInputMode("file");
    setTimeout(() => createInputRef.current?.focus(), 0);
  };

  const openRenameInput = (node) => {
    setCtxMenu(null);
    setRenameNode(node);
    setCreateInputValue(node.name);
    setCreateInputMode("rename");
    setTimeout(() => {
      const input = createInputRef.current;
      if (!input) return;
      input.focus();
      // Select the name without the extension, like most file managers.
      const dot = node.type === "file" ? node.name.lastIndexOf(".") : -1;
      input.setSelectionRange(0, dot > 0 ? dot : node.name.length);
    }, 0);
  };

  const collapseAllFolders = () => {
    setExpandedFolders(new Set());
  };

  /** After a move or rename succeeded on the server: update local copies, preview, selection, mirror. */
  const applyPathChange = (fromPath, toPath, isFolder) => {
    relocateLocalEntry(fromPath, toPath, isFolder);
    onMovePath?.(fromPath, toPath, isFolder);
    void withNotesRoot((root) =>
      isFolder
        ? mirrorMoveFolder(root, fromPath, toPath)
        : mirrorMoveFile(root, fromPath, toPath),
    );
    setExpandedFolders((prev) => new Set([...prev].map((p) => remapPath(p, fromPath, toPath))));
    setSelectedItem((sel) => (sel ? remapPath(sel, fromPath, toPath) : sel));
  };

  const submitRename = async (node, newName) => {
    if (newName === node.name) return;
    const isFolder = node.type === "folder";
    try {
      const formData = new FormData();
      formData.append("path", sanitizeVirtualPath(node.path));
      formData.append("new_name", newName);
      const res = await authFetch(`${API_BASE}/rename_path`, { method: "POST", body: formData });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        alert(apiErrorMessage(data, res));
        return;
      }
      const toPath = sanitizeVirtualPath(data.path || "");
      if (toPath) applyPathChange(node.path, toPath, isFolder);
      fetchTree();
    } catch (err) {
      alert(`Rename failed. ${errorText(err)}`);
    }
  };

  const submitCreateInput = async () => {
    const name = createInputValue.trim();
    if (!name) return;
    const mode = createInputMode;
    const parentForCreate = parentPathForCreate;
    const target = renameNode;

    closeCreateInput();
    if (mode === "rename") {
      if (target) await submitRename(target, name);
      return;
    }
    try {
      const formData = new FormData();
      formData.append("path", sanitizeVirtualPath(parentForCreate));
      formData.append("name", name);
      const url = mode === "folder" ? `${API_BASE}/create_folder` : `${API_BASE}/create_file`;
      const res = await authFetch(url, { method: "POST", body: formData });
      if (res.ok) {
        const data = await res.json().catch(() => ({}));
        const parent = (parentForCreate || "").replace(/\\/g, "/");
        void withNotesRoot(async (root) => {
          if (mode === "folder") {
            const rel = sanitizeVirtualPath(parent ? `${parent}/${name}` : name);
            await mirrorEnsureDir(root, rel);
          } else {
            const rel = sanitizeVirtualPath(data.path || "");
            if (rel) await mirrorWriteFile(root, rel, new Blob([]));
          }
        });
        fetchTree();
        setExpandedFolders((prev) => {
          const n = new Set(prev);
          const parent = (parentForCreate || "").replace(/\\/g, "/");
          if (parent) n.add(parent);
          if (mode === "folder") {
            const rel = parent ? `${parent}/${name}` : name.replace(/\\/g, "/");
            n.add(rel);
          }
          return n;
        });
        if (mode === "file" && data.path) {
          // Open the new file so the user can type notes into it right away (Text view).
          const rel = sanitizeVirtualPath(data.path);
          setSelectedItem(rel);
          setSelectedIsFolder(false);
          onFileSelect?.(null, rel.split("/").pop(), rel);
        }
      } else {
        const data = await res.json().catch(() => ({}));
        alert(apiErrorMessage(data, res));
      }
    } catch (err) {
      alert(`Failed to create ${mode === "folder" ? "folder" : "file"}. ${errorText(err)}`);
    }
  };

  const handleMove = async (fromPath, toFolder) => {
    if (!fromPath || toFolder === undefined) return;
    if (toFolder === fromPath || (fromPath + "/").startsWith(toFolder + "/")) return;
    const isFolder = !!dragSource?.isFolder;
    try {
      const formData = new FormData();
      formData.append("from_path", sanitizeVirtualPath(fromPath));
      formData.append("to_folder", sanitizeVirtualPath(toFolder));
      const res = await authFetch(`${API_BASE}/move_path`, { method: "POST", body: formData });
      if (res.ok) {
        const data = await res.json().catch(() => ({}));
        const toPath = (data.path || "").replace(/\\/g, "/").replace(/^\/+/, "");
        if (toPath) applyPathChange(fromPath, toPath, isFolder);
        fetchTree();
      } else {
        const data = await res.json().catch(() => ({}));
        alert(apiErrorMessage(data, res));
      }
    } catch (err) {
      alert(`Move failed. ${errorText(err)}`);
    } finally {
      setDragSource(null);
      setDropTarget(null);
    }
  };

  const deleteNode = async (node) => {
    const isFolder = node.type === "folder";
    if (!window.confirm(isFolder ? `Delete folder "${node.name}" and all its contents?` : `Delete file "${node.name}"?`)) return;
    setCtxMenu(null);
    const formData = new FormData();
    formData.append("path", sanitizeVirtualPath(node.path));
    formData.append("kind", isFolder ? "folder" : "file");
    try {
      const res = await authFetch(`${API_BASE}/delete_path`, { method: "POST", body: formData });
      if (res.ok) {
        if (isFolder) forgetPathPrefix(node.path);
        else forgetPath(node.path);
        void withNotesRoot((root) => mirrorRemove(root, node.path, isFolder));
        onDeletePath?.(node.path, isFolder);
        fetchTree();
      } else {
        const data = await res.json().catch(() => ({}));
        alert(apiErrorMessage(data, res));
      }
    } catch (err) {
      alert(`Delete failed. ${errorText(err)}`);
    }
  };

  const toggleMainSource = async (node) => {
    setCtxMenu(null);
    const isMain = !!fileMeta[node.path]?.is_primary_authority;
    try {
      const formData = new FormData();
      formData.append("path", sanitizeVirtualPath(node.path));
      formData.append("is_primary_authority", isMain ? "false" : "true");
      const res = await authFetch(`${API_BASE}/file_meta`, { method: "POST", body: formData });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        alert(apiErrorMessage(data, res));
        return;
      }
      setFileMeta((prev) => ({ ...prev, [node.path]: { ...(prev[node.path] || {}), ...(data.entry || {}), is_primary_authority: !isMain } }));
    } catch (err) {
      alert(`Couldn't update the file. ${errorText(err)}`);
    }
  };

  const canDropOn = (targetFolderPath) => {
    if (!dragSource) return false;
    if (targetFolderPath === dragSource.path) return false;
    if (dragSource.isFolder && (targetFolderPath + "/").startsWith(dragSource.path + "/")) return false;
    return true;
  };

  /** Files dragged in from the desktop (not a row dragged inside the explorer). */
  const isExternalFileDrag = (e) =>
    !dragSource && Array.from(e.dataTransfer?.types || []).includes("Files");

  const uploadPath = selectedIsFolder && selectedItem ? selectedItem : "";

  // ─── Recursive tree renderer ─────────────────────────────────────────────

  const renderNode = (node, depth = 0) => {
    const INDENT = 8; // px per depth level (matches VS Code's tight spacing)

    if (node.type === "folder") {
      const isExpanded = expandedFolders.has(node.path);
      const isDropTarget = dropTarget === node.path && (externalDragOver || canDropOn(node.path));
      const isSelected = selectedItem === node.path;

      return (
        <div key={node.path} className="vsc2-node">
          <div
            className={`vsc2-row${isSelected ? " vsc2-row--selected" : ""}${isDropTarget ? " vsc2-row--drop" : ""}`}
            style={{ paddingLeft: `${depth * INDENT + 4}px` }}
            data-path={node.path}
            onClick={() => {
              setSelectedItem(node.path);
              setSelectedIsFolder(true);
              toggleFolder(node.path);
            }}
            draggable
            onDragStart={(e) => {
              setDragSource({ path: node.path, isFolder: true });
              e.dataTransfer.setData("text/plain", node.path);
              e.dataTransfer.effectAllowed = "move";
            }}
            onDragOver={(e) => {
              e.preventDefault();
              if (isExternalFileDrag(e)) {
                e.dataTransfer.dropEffect = "copy";
                setDropTarget(node.path);
                return;
              }
              e.dataTransfer.dropEffect = "move";
              if (canDropOn(node.path)) setDropTarget(node.path);
            }}
            onDragLeave={() => setDropTarget((t) => (t === node.path ? null : t))}
            onDrop={(e) => {
              e.preventDefault();
              if (isExternalFileDrag(e)) {
                e.stopPropagation();
                setExternalDragOver(false);
                setDropTarget(null);
                setExpandedFolders((prev) => new Set(prev).add(node.path));
                void uploads.uploadFiles(e.dataTransfer.files, node.path);
                return;
              }
              if (dropTarget === node.path && dragSource) handleMove(dragSource.path, node.path);
              setDropTarget(null);
            }}
            onDragEnd={() => { setDragSource(null); setDropTarget(null); }}
          >
            {/* Indent guides */}
            {Array.from({ length: depth }).map((_, i) => (
              <span
                key={i}
                className="vsc2-indent-guide"
                style={{ left: `${i * INDENT + 8}px` }}
              />
            ))}

            <span
              className="vsc2-row-name-hit"
              onContextMenu={(e) => openContextMenu(e, node)}
            >
              <span className={`vsc2-chevron${isExpanded ? " vsc2-chevron--open" : ""}`}>
                <svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor" aria-hidden>
                  <path d="M6 4l4 4-4 4V4Z" />
                </svg>
              </span>
              <span className="vsc2-label">{node.name}</span>
            </span>
          </div>

          {isExpanded && (
            <div className="vsc2-children">
              {(node.children || []).map((child) => renderNode(child, depth + 1))}
            </div>
          )}
        </div>
      );
    }

    // File row
    const isSelected = selectedItem === node.path;
    const isMain = !!fileMeta[node.path]?.is_primary_authority;
    return (
      <div
        key={node.path}
        className={`vsc2-row vsc2-row--file${isSelected ? " vsc2-row--selected" : ""}`}
        style={{ paddingLeft: `${depth * INDENT + 4 + 16}px` }}  /* +16 for chevron space */
        data-path={node.path}
        onClick={() => {
          setSelectedItem(node.path);
          setSelectedIsFolder(false);
          onFileSelect?.(getFileUrl(node.path) || null, node.name, node.path);
        }}
        draggable
        onDragStart={(e) => {
          setDragSource({ path: node.path, isFolder: false });
          e.dataTransfer.setData("text/plain", node.path);
          e.dataTransfer.effectAllowed = "move";
        }}
        onDragEnd={() => { setDragSource(null); setDropTarget(null); }}
      >
        {/* Indent guides for files */}
        {Array.from({ length: depth }).map((_, i) => (
          <span
            key={i}
            className="vsc2-indent-guide"
            style={{ left: `${i * INDENT + 8}px` }}
          />
        ))}

        <span className="vsc2-row-name-hit" onContextMenu={(e) => openContextMenu(e, node)}>
          <FileTypeIcon name={node.name} size={15} />
          <span className="vsc2-label">{node.name}</span>
          {isMain && (
            <span className="vsc2-main-star" title="Main source" aria-label="Main source">
              ★
            </span>
          )}
        </span>
      </div>
    );
  };

  const handleLinkDiskFolder = async () => {
    try {
      const linked = await linkNoteScannerFolder();
      if (linked) {
        alert(`Linked folder "${linked.name}" for local mirroring.`);
      }
    } catch (e) {
      if (e?.name !== "AbortError") {
        alert(e?.message || "Could not link your NoteScanner folder.");
      }
    }
  };

  const createPlaceholder =
    createInputMode === "rename"
      ? `New name for ${renameNode?.name || "item"}`
      : createInputMode === "folder"
        ? (parentPathForCreate ? `New folder in ${parentPathForCreate}` : "New folder name…")
        : (parentPathForCreate ? `New file in ${parentPathForCreate} (e.g. notes.txt)` : "New file name (e.g. notes.txt)");

  const ctxIsMain = ctxMenu?.node?.type === "file" && !!fileMeta[ctxMenu.node.path]?.is_primary_authority;

  return (
    <div className={`vsc2-explorer${externalDragOver ? " vsc2-explorer--file-drag" : ""}`}>
        {/* Header — VS Code–style title + toolbar */}
        <div className="vsc2-header">
          <span
            className="vsc2-header-title"
            onClick={clearTreeSelection}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                clearTreeSelection();
              }
            }}
            role="button"
            tabIndex={0}
            title="Clear selection — new folder/file goes to workspace root"
          >
            NOTESCANNER
          </span>
          <div className="vsc2-header-actions">
            <div className="vsc2-header-menu-wrap" ref={newFileMenuWrapRef}>
              <button
                type="button"
                className="vsc2-header-btn"
                onClick={() => setNewFileMenuOpen((o) => !o)}
                title="New file or upload"
                aria-expanded={newFileMenuOpen}
                aria-haspopup="menu"
                aria-label="New file or upload"
              >
                <VscNewFile size={16} />
              </button>
              {newFileMenuOpen && (
                <div className="vsc2-dropdown" role="menu">
                  <button
                    type="button"
                    className="vsc2-dropdown-item"
                    role="menuitem"
                    onClick={() => {
                      setNewFileMenuOpen(false);
                      openCreateFileInput();
                    }}
                  >
                    New empty file…
                  </button>
                  <button
                    type="button"
                    className="vsc2-dropdown-item"
                    role="menuitem"
                    onClick={() => {
                      setNewFileMenuOpen(false);
                      fileInputRef.current?.click();
                    }}
                  >
                    Upload files…
                  </button>
                  <label className="vsc2-dropdown-item vsc2-dropdown-check">
                    <input
                      type="checkbox"
                      className="upload-auto-flashcards"
                      checked={autoFlashcards}
                      onChange={(e) => toggleAutoFlashcards(e.target.checked)}
                    />
                    <span>Make flashcards automatically</span>
                  </label>
                </div>
              )}
            </div>
            <button
              type="button"
              className="vsc2-header-btn"
              onClick={openCreateFolderInput}
              title="New Folder"
              aria-label="New Folder"
            >
              <VscNewFolder size={16} />
            </button>
            <button
              type="button"
              className="vsc2-header-btn"
              onClick={handleLinkDiskFolder}
              title="Link NoteScanner folder on disk"
              aria-label="Link NoteScanner folder on disk"
            >
              <VscFolderOpened size={16} />
            </button>
            <button
              type="button"
              className="vsc2-header-btn"
              onClick={() => fetchTree()}
              title="Refresh Explorer"
              aria-label="Refresh Explorer"
            >
              <VscRefresh size={16} />
            </button>
            <button
              type="button"
              className="vsc2-header-btn"
              onClick={collapseAllFolders}
              title="Collapse Folders in Explorer"
              aria-label="Collapse Folders in Explorer"
            >
              <VscCollapseAll size={16} />
            </button>
          </div>
        </div>

        {createInputMode && (
          <div className="vsc2-create-row" ref={createRowRef}>
            <input
              ref={createInputRef}
              type="text"
              className="vsc2-create-input"
              placeholder={createPlaceholder}
              aria-label={createPlaceholder}
              value={createInputValue}
              onChange={(e) => setCreateInputValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") submitCreateInput();
                if (e.key === "Escape") closeCreateInput();
              }}
            />
            <button
              type="button"
              className="vsc2-create-btn"
              onClick={submitCreateInput}
              disabled={!createInputValue.trim()}
            >
              {createInputMode === "rename" ? "Rename" : "OK"}
            </button>
          </div>
        )}

        <FileUpload ref={fileInputRef} onFiles={(files) => uploads.uploadFiles(files, uploadPath)} />

        <UploadProgress
          items={uploads.items}
          onDismiss={uploads.dismiss}
          onClearFinished={uploads.clearFinished}
          onMakeFlashcards={(item) => makeFlashcards(item.key, item.path)}
        />

        {ctxMenu && (
          <div
            className="vsc2-context-menu"
            style={{ left: ctxMenu.x, top: ctxMenu.y }}
            role="menu"
          >
            <button
              type="button"
              className="vsc2-context-menu-item vsc2-context-menu-item--rename"
              role="menuitem"
              onClick={() => openRenameInput(ctxMenu.node)}
            >
              Rename…
            </button>
            {ctxMenu.node.type === "file" && (
              <button
                type="button"
                className="vsc2-context-menu-item vsc2-context-menu-item--main-source"
                role="menuitem"
                onClick={() => toggleMainSource(ctxMenu.node)}
              >
                {ctxIsMain ? "Unmark main source" : "Mark as main source"}
              </button>
            )}
            <button
              type="button"
              className="vsc2-context-menu-item"
              role="menuitem"
              onClick={() => copyNodePath(ctxMenu.node)}
            >
              Copy path
            </button>
            <button
              type="button"
              className="vsc2-context-menu-item vsc2-context-menu-item--danger"
              role="menuitem"
              onClick={() => deleteNode(ctxMenu.node)}
            >
              Delete…
            </button>
          </div>
        )}

        <div
          className="vsc2-tree"
          onMouseDown={handleTreeMouseDown}
          onDragOver={(e) => {
            if (!isExternalFileDrag(e)) return;
            e.preventDefault();
            e.dataTransfer.dropEffect = "copy";
            if (!externalDragOver) setExternalDragOver(true);
          }}
          onDragLeave={(e) => {
            if (e.currentTarget.contains(e.relatedTarget)) return;
            setExternalDragOver(false);
            setDropTarget(null);
          }}
          onDrop={(e) => {
            if (!isExternalFileDrag(e)) return;
            e.preventDefault();
            setExternalDragOver(false);
            setDropTarget(null);
            void uploads.uploadFiles(e.dataTransfer.files, uploadPath);
          }}
        >
          {dragSource && (
            <div
              className={`vsc2-root-drop${dropTarget === "" ? " vsc2-row--drop" : ""}`}
              onDragOver={(e) => { e.preventDefault(); setDropTarget(""); }}
              onDragLeave={() => setDropTarget((t) => (t === "" ? null : t))}
              onDrop={(e) => { e.preventDefault(); if (dragSource) handleMove(dragSource.path, ""); setDropTarget(null); }}
            >
              Move to root
            </div>
          )}

          {externalDragOver && (
            <div className="vsc2-drop-hint">
              Drop files on a folder, or anywhere here to upload to{" "}
              {uploadPath ? `“${uploadPath}”` : "the top level"}.
            </div>
          )}

          {treeError ? (
            <div className="vsc2-empty vsc2-error" role="alert">
              {treeError}{" "}
              <button type="button" className="vsc2-retry" onClick={() => fetchTree()}>
                Retry
              </button>
            </div>
          ) : tree.length === 0 ? (
            <div className="vsc2-empty">
              No files yet. Use New File or New Folder (click the title above to target the root),
              or drag files here to upload them.
            </div>
          ) : (
            tree.map((node) => renderNode(node, 0))
          )}
        </div>
      </div>
  );
}
