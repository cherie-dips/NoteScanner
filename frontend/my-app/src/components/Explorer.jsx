import { useEffect, useState, useRef } from "react";
import FileUpload from "./FileUpload";
import { API_BASE } from "../config";
import "../index.css";

export default function Explorer({ onFileSelect }) {
  const [tree, setTree] = useState([]);
  const [expandedFolders, setExpandedFolders] = useState(new Set());
  const [selectedItem, setSelectedItem] = useState(null);
  const [selectedIsFolder, setSelectedIsFolder] = useState(false);
  const [showNewFolderInput, setShowNewFolderInput] = useState(false);
  const [newFolderName, setNewFolderName] = useState("");
  const [showNewFileInput, setShowNewFileInput] = useState(false);
  const [newFileName, setNewFileName] = useState("");
  const newFolderInputRef = useRef(null);
  const newFileInputRef = useRef(null);
  const fileInputRef = useRef(null);
  const folderInputRef = useRef(null);

  const fetchTree = async () => {
    const res = await fetch(`${API_BASE}/list_tree`);
    const data = await res.json();
    setTree(data.tree);
  };

  useEffect(() => { fetchTree(); }, []);

  useEffect(() => {
    if (showNewFolderInput && newFolderInputRef.current) newFolderInputRef.current.focus();
  }, [showNewFolderInput]);
  useEffect(() => {
    if (showNewFileInput && newFileInputRef.current) newFileInputRef.current.focus();
  }, [showNewFileInput]);

  const toggleFolder = (folderPath) => {
    setExpandedFolders((prev) => {
      const next = new Set(prev);
      next.has(folderPath) ? next.delete(folderPath) : next.add(folderPath);
      return next;
    });
  };

  const handleCreateFolder = async (e) => {
    e?.preventDefault();
    const name = newFolderName.trim();
    if (!name) { setShowNewFolderInput(false); return; }

    const parentPath = selectedItem && selectedIsFolder ? selectedItem : "";
    const formData = new FormData();
    formData.append("path", parentPath);
    formData.append("name", name);

    const res = await fetch(`${API_BASE}/create_folder`, { method: "POST", body: formData });
    if (res.ok) {
      setNewFolderName("");
      setShowNewFolderInput(false);
      fetchTree();
      if (parentPath) setExpandedFolders((prev) => new Set(prev).add(parentPath));
    }
  };

  const handleNewFolderKeyDown = (e) => {
    if (e.key === "Enter") handleCreateFolder(e);
    if (e.key === "Escape") { setShowNewFolderInput(false); setNewFolderName(""); }
  };

  const handleCreateFile = async (e) => {
    e?.preventDefault();
    const name = newFileName.trim();
    if (!name) { setShowNewFileInput(false); return; }
    const parentPath = selectedItem && selectedIsFolder ? selectedItem : "";
    const formData = new FormData();
    formData.append("path", parentPath);
    formData.append("name", name);
    const res = await fetch(`${API_BASE}/create_file`, { method: "POST", body: formData });
    if (res.ok) {
      setNewFileName("");
      setShowNewFileInput(false);
      fetchTree();
      if (parentPath) setExpandedFolders((prev) => new Set(prev).add(parentPath));
    }
  };

  const handleNewFileKeyDown = (e) => {
    if (e.key === "Enter") handleCreateFile(e);
    if (e.key === "Escape") { setShowNewFileInput(false); setNewFileName(""); }
  };

  const handleFolderUpload = async (e) => {
    const files = e.target.files;
    if (!files?.length) return;
    const path = selectedItem && selectedIsFolder ? selectedItem : "";
    for (let i = 0; i < files.length; i++) {
      const formData = new FormData();
      formData.append("path", path);
      formData.append("file", files[i]);
      formData.append("auto_extract", "true");
      try {
        await fetch(`${API_BASE}/upload_note`, { method: "POST", body: formData });
      } catch (_) {}
    }
    e.target.value = "";
    fetchTree();
    if (files.length) alert(`${files.length} file(s) uploaded.`);
  };

  const renderNode = (node, depth = 0) => {
    if (node.type === "folder") {
      const isExpanded = expandedFolders.has(node.path);
      const hasChildren = node.children?.length > 0;

      return (
        <div key={node.path}>
          <div
            className={`vsc-tree-row${selectedItem === node.path ? " selected" : ""}`}
            style={{ paddingLeft: `${depth * 12}px` }}
            onClick={() => {
              setSelectedItem(node.path);
              setSelectedIsFolder(true);
              toggleFolder(node.path);
            }}
          >
            <span className={`vsc-chevron${isExpanded ? " open" : ""}${!hasChildren ? " hidden" : ""}`}>
              <svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor"><path d="M6 4l4 4-4 4V4Z"/></svg>
            </span>
            <span className="vsc-filename">{node.name}</span>
          </div>
          {isExpanded && hasChildren && (
            <div className="vsc-subtree" style={{ "--depth": depth }}>
              {node.children.map((child) => renderNode(child, depth + 1))}
            </div>
          )}
        </div>
      );
    }

    return (
      <div
        key={node.path}
        className={`vsc-tree-row file${selectedItem === node.path ? " selected" : ""}`}
        style={{ paddingLeft: `${depth * 12 + 20}px` }}
        onClick={() => {
          setSelectedItem(node.path);
          setSelectedIsFolder(false);
          onFileSelect?.(`${API_BASE}/user_notes/${node.path}`, node.name);
        }}
      >
        <span className="vsc-chevron hidden" aria-hidden><svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor"><path d="M6 4l4 4-4 4V4Z"/></svg></span>
        <span className="vsc-filename">{node.name}</span>
      </div>
    );
  };

  const uploadPath = selectedIsFolder && selectedItem ? selectedItem : "";

  return (
    <div className="explorer-panel">
      {/* VS Code Explorer Header */}
      <div className="vsc-panel-header">
        <span className="vsc-panel-title">EXPLORER</span>
        <div className="vsc-panel-actions">
          <button className="vsc-icon-btn" onClick={() => { setShowNewFileInput(true); setNewFileName(""); }} title="Create File">
            <svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.15" strokeLinecap="round" strokeLinejoin="round">
              <path d="M4 2h5l4 4v7a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1Z"/>
              <path d="M9 2v4h4"/>
              <line x1="7" y1="9" x2="9" y2="9"/>
              <line x1="8" y1="8" x2="8" y2="10"/>
            </svg>
          </button>
          <button className="vsc-icon-btn" onClick={() => { setShowNewFolderInput(true); setNewFolderName(""); }} title="Create Folder">
            <svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.15" strokeLinecap="round" strokeLinejoin="round">
              <path d="M2 4.5a1 1 0 0 1 1-1h2.879a1 1 0 0 1 .707.293L8 5.5H13a1 1 0 0 1 1 1V11a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V4.5Z"/>
              <line x1="8" y1="7.5" x2="8" y2="10.5"/>
              <line x1="6.5" y1="9" x2="9.5" y2="9"/>
            </svg>
          </button>
          <button className="vsc-icon-btn" onClick={() => fileInputRef.current?.click()} title="Upload File">
            <svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.15" strokeLinecap="round" strokeLinejoin="round">
              <path d="M2 12V9a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v3"/>
              <path d="M8 4v8M5 7l3-3 3 3"/>
            </svg>
          </button>
          <button className="vsc-icon-btn" onClick={() => folderInputRef.current?.click()} title="Upload Folder">
            <svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.15" strokeLinecap="round" strokeLinejoin="round">
              <path d="M2 4.5a1 1 0 0 1 1-1h2.879a1 1 0 0 1 .707.293L8 5.5H13a1 1 0 0 1 1 1V11a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V4.5Z"/>
              <path d="M2 8h12"/>
            </svg>
          </button>
        </div>
      </div>

      <FileUpload ref={fileInputRef} path={uploadPath} onFileUploaded={fetchTree} hidden />
      <input
        ref={folderInputRef}
        type="file"
        webkitdirectory
        multiple
        onChange={handleFolderUpload}
        style={{ display: "none" }}
        aria-hidden
      />

      {showNewFileInput && (
        <div className="vsc-inline-folder-row">
          <span className="vsc-chevron hidden" aria-hidden><svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor"><path d="M6 4l4 4-4 4V4Z"/></svg></span>
          <input
            ref={newFileInputRef}
            className="vsc-rename-input"
            placeholder="File name"
            value={newFileName}
            onChange={(e) => setNewFileName(e.target.value)}
            onBlur={handleCreateFile}
            onKeyDown={handleNewFileKeyDown}
          />
        </div>
      )}

      {showNewFolderInput && (
        <div className="vsc-inline-folder-row">
          <span className="vsc-chevron hidden" aria-hidden><svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor"><path d="M6 4l4 4-4 4V4Z"/></svg></span>
          <input
            ref={newFolderInputRef}
            className="vsc-rename-input"
            placeholder="Folder name"
            value={newFolderName}
            onChange={(e) => setNewFolderName(e.target.value)}
            onBlur={handleCreateFolder}
            onKeyDown={handleNewFolderKeyDown}
          />
        </div>
      )}

      {/* File tree */}
      <div className="vsc-file-tree">
        {tree.map((node) => renderNode(node))}
      </div>
    </div>
  );
}