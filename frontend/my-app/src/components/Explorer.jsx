import { useEffect, useState, useRef } from "react";
import { HiOutlineDocumentPlus, HiOutlineFolderPlus } from "react-icons/hi2";
import { BsFileEarmarkPdf } from "react-icons/bs";
import { AiOutlineDelete } from "react-icons/ai";
import FileUpload from "./FileUpload";
import { API_BASE } from "../config";
import { authFetch, getFileUrl, ensureGuestId } from "../auth";

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

export default function Explorer({ onFileSelect, onDeletePath }) {
  const [tree, setTree] = useState([]);
  const [expandedFolders, setExpandedFolders] = useState(new Set());
  const [selectedItem, setSelectedItem] = useState(null);
  const [selectedIsFolder, setSelectedIsFolder] = useState(false);
  const [showCreateInput, setShowCreateInput] = useState(false);
  const [newFolderName, setNewFolderName] = useState("");
  const [dragSource, setDragSource] = useState(null);
  const [dropTarget, setDropTarget] = useState(null);
  const fileInputRef = useRef(null);
  const createInputRef = useRef(null);
  const createRowRef = useRef(null);

  const fetchTree = async () => {
    const res = await authFetch(`${API_BASE}/list_tree`);
    const data = await res.json();
    setTree(data.tree || []);
  };

  useEffect(() => {
    ensureGuestId().then(() => fetchTree());
  }, []);

  useEffect(() => {
    if (!showCreateInput) return;
    const handleClickOutside = (e) => {
      if (createRowRef.current && !createRowRef.current.contains(e.target)) {
        setShowCreateInput(false);
        setNewFolderName("");
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [showCreateInput]);

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

  const openCreateInput = () => {
    setNewFolderName("");
    setShowCreateInput(true);
    setTimeout(() => createInputRef.current?.focus(), 0);
  };

  const submitCreateFolder = async () => {
    const name = newFolderName.trim();
    if (!name) return;
    setShowCreateInput(false);
    setNewFolderName("");
    try {
      const formData = new FormData();
      formData.append("path", parentPathForCreate);
      formData.append("name", name);
      const res = await authFetch(`${API_BASE}/create_folder`, { method: "POST", body: formData });
      if (res.ok) {
        fetchTree();
        if (parentPathForCreate) setExpandedFolders((prev) => new Set(prev).add(parentPathForCreate));
      } else {
        const data = await res.json().catch(() => ({}));
        alert(data.error || "Failed to create folder.");
      }
    } catch (_) {
      alert("Failed to create folder.");
    }
  };

  const handleMove = async (fromPath, toFolder) => {
    if (!fromPath || toFolder === undefined) return;
    if (toFolder === fromPath || (fromPath + "/").startsWith(toFolder + "/")) return;
    try {
      const formData = new FormData();
      formData.append("from_path", fromPath);
      formData.append("to_folder", toFolder);
      const res = await authFetch(`${API_BASE}/move_path`, { method: "POST", body: formData });
      if (res.ok) {
        onDeletePath?.(fromPath, false);
        fetchTree();
      } else {
        const data = await res.json().catch(() => ({}));
        alert(data.error || "Move failed.");
      }
    } catch (_) {
      alert("Move failed.");
    } finally {
      setDragSource(null);
      setDropTarget(null);
    }
  };

  const handleDelete = async (e, node) => {
    e.stopPropagation();
    const isFolder = node.type === "folder";
    if (!window.confirm(isFolder ? `Delete folder "${node.name}" and all its contents?` : `Delete file "${node.name}"?`)) return;
    const formData = new FormData();
    formData.append("path", node.path);
    formData.append("kind", isFolder ? "folder" : "file");
    const res = await authFetch(`${API_BASE}/delete_path`, { method: "POST", body: formData });
    if (res.ok) {
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

  const renderNode = (node, depth = 0, isLast = false, parentLines = []) => {
    const INDENT = 8; // px per depth level (matches VS Code's tight spacing)

    if (node.type === "folder") {
      const isExpanded = expandedFolders.has(node.path);
      const hasChildren = node.children?.length > 0;
      const isDropTarget = dropTarget === node.path && canDropOn(node.path);
      const isSelected = selectedItem === node.path;

      return (
        <div key={node.path} className="vsc2-node">
          <div
            className={`vsc2-row${isSelected ? " vsc2-row--selected" : ""}${isDropTarget ? " vsc2-row--drop" : ""}`}
            style={{ paddingLeft: `${depth * INDENT + 4}px` }}
            onClick={(e) => {
              if (e.target.closest(".vsc2-delete")) return;
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

            {/* Chevron */}
            <span className={`vsc2-chevron${isExpanded ? " vsc2-chevron--open" : ""}`}>
              {hasChildren ? (
                <svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor">
                  <path d="M6 4l4 4-4 4V4Z" />
                </svg>
              ) : null}
            </span>

            <span className="vsc2-label">{node.name}</span>

            <button type="button" className="vsc2-delete" onClick={(e) => handleDelete(e, node)} title="Delete folder" aria-label="Delete folder">
              <AiOutlineDelete size={14} />
            </button>
          </div>

          {/* Children */}
          {isExpanded && hasChildren && (
            <div className="vsc2-children">
              {node.children.map((child, i) =>
                renderNode(child, depth + 1, i === node.children.length - 1, [...parentLines])
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
        onClick={(e) => {
          if (e.target.closest(".vsc2-delete")) return;
          setSelectedItem(node.path);
          setSelectedIsFolder(false);
          onFileSelect?.(getFileUrl(node.path), node.name);
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

        <FileTypeIcon name={node.name} size={15} />
        <span className="vsc2-label">{node.name}</span>
        <button type="button" className="vsc2-delete" onClick={(e) => handleDelete(e, node)} title="Delete file" aria-label="Delete file">
          <AiOutlineDelete size={14} />
        </button>
      </div>
    );
  };

  const uploadPath = selectedIsFolder && selectedItem ? selectedItem : "";

  return (
    <div className="vsc2-explorer">
        {/* Header */}
        <div className="vsc2-header">
          <span className="vsc2-header-title">EXPLORER</span>
          <div className="vsc2-header-actions">
            <button
              type="button"
              className="vsc2-header-btn"
              onClick={() => fileInputRef.current?.click()}
              title="Upload File"
              aria-label="Upload file"
            >
              <HiOutlineDocumentPlus size={16} />
            </button>
            <button
              type="button"
              className="vsc2-header-btn"
              onClick={openCreateInput}
              title="New Folder"
              aria-label="New Folder"
            >
              <HiOutlineFolderPlus size={16} />
            </button>
          </div>
        </div>

        {/* Create folder input */}
        {showCreateInput && (
          <div className="vsc2-create-row" ref={createRowRef}>
            <input
              ref={createInputRef}
              type="text"
              className="vsc2-create-input"
              placeholder={parentPathForCreate ? `Folder in ${parentPathForCreate}` : "Folder name…"}
              value={newFolderName}
              onChange={(e) => setNewFolderName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") submitCreateFolder();
                if (e.key === "Escape") { setShowCreateInput(false); setNewFolderName(""); }
              }}
            />
            <button
              type="button"
              className="vsc2-create-btn"
              onClick={submitCreateFolder}
              disabled={!newFolderName.trim()}
            >OK</button>
          </div>
        )}

        <FileUpload ref={fileInputRef} path={uploadPath} onFileUploaded={fetchTree} hidden />

        {/* Tree */}
        <div className="vsc2-tree">
          {/* Root drop zone (visible only while dragging) */}
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
            <div className="vsc2-empty">No files yet. Upload a file or create a folder.</div>
          ) : (
            tree.map((node, i) => renderNode(node, 0, i === tree.length - 1))
          )}
        </div>
      </div>
  );
}