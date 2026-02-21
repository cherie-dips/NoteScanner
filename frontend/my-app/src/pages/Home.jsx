import { useState } from "react";
import Explorer from "../components/Explorer";
import QueryInterface from "../components/QueryInterface";
import "../index.css"

export default function Home() {
  const [preview, setPreview] = useState(null);

  const handleFileSelect = (fileUrl) => {
    setPreview(fileUrl);
  };

  return (
    <div className="app-container">
      <div className="left-sidebar">
        <div className="left-sidebar-content">
          <Explorer onFileSelect={handleFileSelect} />
        </div>
      </div>

      <div className="preview-pane">
        <div className="preview-content">
          {preview ? (
            preview.endsWith(".pdf") ? (
              <embed src={preview} type="application/pdf" />
            ) : (
              <img src={preview} alt="preview" />
            )
          ) : (
            <div className="preview-placeholder">
              <div className="preview-placeholder-icon">📄</div>
              <p>Select a file from the explorer to preview it here</p>
            </div>
          )}
        </div>
      </div>

      <div className="right-sidebar">
        <div className="right-sidebar-content">
          <QueryInterface />
        </div>
      </div>
    </div>
  );
}