import { useState } from "react";
import Explorer from "../components/Explorer";
import QueryInterface from "../components/QueryInterface";
import "../index.css"

export default function Home() {
  const [preview, setPreview] = useState(null);
  const [previewName, setPreviewName] = useState("");

  const handleFileSelect = (fileUrl, fileName) => {
    setPreview(fileUrl);
    setPreviewName(fileName);
  };

  return (
    <div className="app-container">
      {/* Left Sidebar - File Explorer */}
      <div className="left-sidebar">
        <div className="left-sidebar-header">
          📁 File Explorer
        </div>
        <div className="left-sidebar-content">
          <Explorer onFileSelect={handleFileSelect} />
        </div>
      </div>

      {/* Middle - Preview Pane */}
      <div className="preview-pane">
        <div className="preview-pane-header">
          <span>📄 Document Preview</span>
          {previewName && (
            <span className="preview-filename">{previewName}</span>
          )}
        </div>
        <div className="preview-content">
          {preview ? (
            preview.endsWith(".pdf") ? (
              <embed src={preview} type="application/pdf" />
            ) : (
              <img src={preview} alt="preview" />
            )
          ) : (
            <div style={{ 
              display: "flex", 
              flexDirection: "column",
              justifyContent: "center", 
              alignItems: "center", 
              height: "100%",
              color: "#cccccc",
              fontSize: "14px"
            }}>
              <div style={{ fontSize: "3rem", marginBottom: "1rem", opacity: 0.6 }}>📄</div>
              <p style={{ opacity: 0.8 }}>Select a file from the explorer to preview it here</p>
            </div>
          )}
        </div>
      </div>

      {/* Right Sidebar - Query Interface */}
      <div className="right-sidebar">
        <div className="right-sidebar-header">
          🤖 AI Assistant
        </div>
        <div className="right-sidebar-content">
          <QueryInterface />
        </div>
      </div>
    </div>
  );
}