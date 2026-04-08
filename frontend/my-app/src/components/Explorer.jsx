import { useEffect, useState, useRef } from "react";
import {
  VscNewFile,
  VscNewFolder,
  VscRefresh,
  VscCollapseAll,
  VscFolderOpened,
} from "react-icons/vsc";
import { BsFileEarmarkPdf } from "react-icons/bs";
import FileUpload from "./FileUpload";
import { API_BASE } from "../config";
import { authFetch, getFileUrl, ensureGuestId } from "../auth";
import {
  forgetPath,
  forgetPathPrefix,
  relocateLocalEntry,
} from "../localFileStore";
import {
  isOpfsMirrorSupported,
  isUserFolderPickerSupported,
  hasUserDiskRootLinkedSync,
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

// ─── Main Component ───────────────────────────────────────────────────────────

export default function Explorer({
  onFileSelect,
  onDeletePath,
  onMovePath,
  onTreeChange,
}) {
  const [tree, setTree] = useState([]);
  const [expandedFolders, setExpandedFolders] = useState(new Set());
  const [selectedItem, setSelectedItem] = useState(null);
  const [selectedIsFolder, setSelectedIsFolder] = useState(false);
  /** 'folder' | 'file' | null */
  const [createInputMode, setCreateInputMode] = useState(null);
  const [createInputValue, setCreateInputValue] = useState("");
  const [dragSource, setDragSource] = useState(null);
  const [dropTarget, setDropTarget] = useState(null);
  const [newFileMenuOpen, setNewFileMenuOpen] = useState(false);
  const [ctxMenu, setCtxMenu] = useState(null);
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

  const fetchTree = async () => {
    const res = await authFetch(`${API_BASE}/list_tree`);
    const data = await res.json();
    setTree(data.tree || []);
    onTreeChange?.();
  };

  useEffect(() => {
    ensureGuestId().then(() => fetchTree());
  }, []);

  useEffect(() => {
    if (!createInputMode) return;
    const handleClickOutside = (e) => {
      if (createRowRef.current && !createRowRef.current.contains(e.target)) {
        setCreateInputMode(null);
        setCreateInputValue("");
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
    setCreateInputValue("");
    setCreateInputMode("folder");
    setTimeout(() => createInputRef.current?.focus(), 0);
  };

  const openCreateFileInput = () => {
    setCreateInputValue("");
    setCreateInputMode("file");
    setTimeout(() => createInputRef.current?.focus(), 0);
  };

  const collapseAllFolders = () => {
    setExpandedFolders(new Set());
  };

  const submitCreateInput = async () => {
    const name = createInputValue.trim();
    if (!name) return;
    const mode = createInputMode;
    const parentForCreate = parentPathForCreate;

    setCreateInputMode(null);
    setCreateInputValue("");
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
      } else {
        const data = await res.json().catch(() => ({}));
        alert(data.error || `Failed to create ${mode === "folder" ? "folder" : "file"}.`);
      }
    } catch {
      alert(`Failed to create ${mode === "folder" ? "folder" : "file"}.`);
    }
  };

  const handleMove = async (fromPath, toFolder) => {
    if (!fromPath || toFolder === undefined) return;
    if (toFolder === fromPath || (fromPath + "/").startsWith(toFolder + "/")) return;
    try {
      const formData = new FormData();
      formData.append("from_path", sanitizeVirtualPath(fromPath));
      formData.append("to_folder", sanitizeVirtualPath(toFolder));
      const res = await authFetch(`${API_BASE}/move_path`, { method: "POST", body: formData });
      if (res.ok) {
        const data = await res.json().catch(() => ({}));
        const toPath = (data.path || "").replace(/\\/g, "/").replace(/^\/+/, "");
        if (toPath) {
          relocateLocalEntry(fromPath, toPath, !!dragSource.isFolder);
          onMovePath?.(fromPath, toPath, !!dragSource.isFolder);
          const isFolder = !!dragSource.isFolder;
          void withNotesRoot((root) =>
            isFolder
              ? mirrorMoveFolder(root, fromPath, toPath)
              : mirrorMoveFile(root, fromPath, toPath),
          );
        }
        fetchTree();
      } else {
        const data = await res.json().catch(() => ({}));
        alert(data.error || "Move failed.");
      }
    } catch {
      alert("Move failed.");
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
    const res = await authFetch(`${API_BASE}/delete_path`, { method: "POST", body: formData });
    if (res.ok) {
      if (isFolder) forgetPathPrefix(node.path);
      else forgetPath(node.path);
      void withNotesRoot((root) => mirrorRemove(root, node.path, isFolder));
      onDeletePath?.(node.path, isFolder);
      fetchTree();
    } else {
      const data = await res.json().catch(() => ({}));
      alert(data.error || "Delete failed.");
    }
  };
  const canDropOn = (targetFolderPath) => {
    if (!dragSource) return false;
    if (targetFolderPath === dragSource.path) return false;
    if (dragSource.isFolder && (targetFolderPath + "/").startsWith(dragSource.path + "/")) return false;
    return true;
  };

  // ─── Recursive tree renderer ─────────────────────────────────────────────

  const renderNode = (node, depth = 0, parentLines = []) => {
    const INDENT = 8; // px per depth level (matches VS Code's tight spacing)

    if (node.type === "folder") {
      const isExpanded = expandedFolders.has(node.path);
      const isDropTarget = dropTarget === node.path && canDropOn(node.path);
      const isSelected = selectedItem === node.path;

      return (
        <div key={node.path} className="vsc2-node">
          <div
            className={`vsc2-row${isSelected ? " vsc2-row--selected" : ""}${isDropTarget ? " vsc2-row--drop" : ""}`}
            style={{ paddingLeft: `${depth * INDENT + 4}px` }}
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
              e.dataTransfer.dropEffect = "move";
              if (canDropOn(node.path)) setDropTarget(node.path);
            }}
            onDragLeave={() => setDropTarget((t) => (t === node.path ? null : t))}
            onDrop={(e) => {
              e.preventDefault();
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
              {(node.children || []).map((child) =>
                renderNode(child, depth + 1, [...parentLines])
              )}
            </div>
          )}
        </div>
      );
    }

    // File row
    const isSelected = selectedItem === node.path;
    return (
      <div
        key={node.path}
        className={`vsc2-row vsc2-row--file${isSelected ? " vsc2-row--selected" : ""}`}
        style={{ paddingLeft: `${depth * INDENT + 4 + 16}px` }}  /* +16 for chevron space */
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
        </span>
      </div>
    );
  };

  const uploadPath = selectedIsFolder && selectedItem ? selectedItem : "";
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

  return (
    <div className="vsc2-explorer">
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
                    Upload file…
                  </button>
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
              placeholder={
                createInputMode === "folder"
                  ? (parentPathForCreate ? `New folder in ${parentPathForCreate}` : "New folder name…")
                  : (parentPathForCreate ? `New file in ${parentPathForCreate} (e.g. notes.txt)` : "New file name (e.g. notes.txt)")
              }
              value={createInputValue}
              onChange={(e) => setCreateInputValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") submitCreateInput();
                if (e.key === "Escape") {
                  setCreateInputMode(null);
                  setCreateInputValue("");
                }
              }}
            />
            <button
              type="button"
              className="vsc2-create-btn"
              onClick={submitCreateInput}
              disabled={!createInputValue.trim()}
            >
              OK
            </button>
          </div>
        )}

        <FileUpload ref={fileInputRef} path={uploadPath} onFileUploaded={fetchTree} hidden />

        {ctxMenu && (
          <div
            className="vsc2-context-menu"
            style={{ left: ctxMenu.x, top: ctxMenu.y }}
            role="menu"
          >
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

        <div className="vsc2-tree" onMouseDown={handleTreeMouseDown}>
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

          {tree.length === 0 ? (
            <div className="vsc2-empty">No files yet. Use New File or New Folder (click the title above to target the root).</div>
          ) : (
            tree.map((node) => renderNode(node, 0))
          )}
        </div>
      </div>
  );
}