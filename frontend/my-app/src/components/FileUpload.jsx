import { useState, forwardRef } from "react";
import { API_BASE } from "../config";
import { authFetch } from "../auth";

const FileUpload = forwardRef(function FileUpload(
  { path, onFileUploaded, hidden = false },
  ref
) {
  const [uploading, setUploading] = useState(false);

  const doUpload = async (file) => {
    if (!file) return;

    const formData = new FormData();
    formData.append("path", path);
    formData.append("file", file);
    formData.append("auto_extract", "true");

    try {
      setUploading(true);
      const res = await authFetch(`${API_BASE}/upload_note`, {
        method: "POST",
        body: formData,
      });

      if (res.ok) {
        const result = await res.json();
        onFileUploaded?.();
      } else {
        const error = await res.json().catch(() => ({}));
        alert(`Error: ${error.detail || error.error || "Upload failed"}`);
      }
    } catch (error) {
      console.error("Upload error:", error);
      alert("Upload failed. Please try again.");
    } finally {
      setUploading(false);
    }
  };

  const handleChange = async (e) => {
    const file = e.target.files?.[0];
    if (file) {
      await doUpload(file);
    }
    e.target.value = "";
  };

  if (hidden) {
    return (
      <input
        ref={ref}
        type="file"
        accept="image/*,.pdf"
        onChange={handleChange}
        style={{ display: "none" }}
        disabled={uploading}
        aria-hidden
      />
    );
  }

  return (
    <div className="folder-form">
      <input
        ref={ref}
        type="file"
        accept="image/*,.pdf"
        onChange={handleChange}
      />
    </div>
  );
});

export default FileUpload;
