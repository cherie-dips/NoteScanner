import { useEffect, useState } from "react";
import FileUpload from "./FileUpload";
import FolderForm from "./FolderForm";
import "../index.css";

export default function Explorer({ onFileSelect }) {
  const [tree, setTree] = useState([]);
  const [expandedFolders, setExpandedFolders] = useState(new Set());
  const [selectedItem, setSelectedItem] = useState(null);

  const fetchTree = async () => {
    const res = await fetch("http://localhost:8000/list_tree");
    const data = await res.json();
    setTree(data.tree);
  };

  useEffect(() => {
    fetchTree();
  }, []);

  const toggleFolder = (folderPath) => {
    const newExpanded = new Set(expandedFolders);
    if (newExpanded.has(folderPath)) {
      newExpanded.delete(folderPath);
    } else {
      newExpanded.add(folderPath);
    }
    setExpandedFolders(newExpanded);
  };

  const getFileIcon = (fileName) => {
    const ext = fileName.split('.').pop().toLowerCase();
    switch (ext) {
      case 'pdf':
        return '📄';
      case 'txt':
        return '📝';
      case 'md':
        return '📋';
      case 'js':
      case 'jsx':
        return '📜';
      case 'css':
        return '🎨';
      case 'html':
        return '🌐';
      case 'json':
        return '📋';
      case 'py':
        return '🐍';
      default:
        return '📄';
    }
  };

  const renderNode = (node, depth = 0) => {
    if (node.type === "folder") {
      const isExpanded = expandedFolders.has(node.path);
      const hasChildren = node.children && node.children.length > 0;
      
      return (
        <div key={node.path} className="folder">
          <div 
            className={`folder-item ${selectedItem === node.path ? 'selected' : ''}`}
            onClick={() => {
              setSelectedItem(node.path);
              toggleFolder(node.path);
            }}
            style={{ paddingLeft: `${depth * 8 + 8}px` }}
          >
            <span className={`folder-arrow ${isExpanded ? 'expanded' : ''}`}>
              {hasChildren ? '▶' : ''}
            </span>
            <span className="folder-icon">📁</span>
            <span className="folder-name">{node.name}</span>
          </div>
          
          {isExpanded && (
            <div className="folder-children">
              <FolderForm path={node.path} onFolderCreated={fetchTree} />
              <FileUpload path={node.path} onFileUploaded={fetchTree} />
              {node.children.map((child) => renderNode(child, depth + 1))}
            </div>
          )}
        </div>
      );
    } else {
      return (
        <div 
          key={node.path} 
          className={`file-item ${selectedItem === node.path ? 'selected' : ''}`}
          onClick={() => {
            setSelectedItem(node.path);
            const fileUrl = `http://localhost:8000/user_notes/${node.path}`;
            if (onFileSelect) {
              onFileSelect(fileUrl, node.name);
            }
          }}
          style={{ paddingLeft: `${depth * 8 + 24}px` }}
        >
          <span className="file-icon">{getFileIcon(node.name)}</span>
          <span className="file-name">{node.name}</span>
        </div>
      );
    }
  };

  return (
    <div>
      <FolderForm path="" onFolderCreated={fetchTree} />
      {tree.map((node) => renderNode(node))}
    </div>
  );
}