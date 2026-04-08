import { useState, forwardRef } from "react";
import { API_BASE } from "../config";
import { authFetch, apiErrorMessage } from "../auth";
import { registerLocalFile } from "../localFileStore";
import { getNotesMirrorRoot, mirrorUploadedBytes } from "../localDiskFolder";
import { sanitizeVirtualPath } from "../virtualPath";

const FileUpload = forwardRef(function FileUpload(
  { path, onFileUploaded, hidden = false },
  ref
) {
  const [uploading, setUploading] = useState(false);

  const doUpload = async (file) => {
    if (!file) return;

    const formData = new FormData();
    formData.append("path", sanitizeVirtualPath(path));
    formData.append("file", file);
    formData.append("auto_extract", "true");

    try {
      setUploading(true);
      // Resolve folder permission before the network round-trip (keeps user activation useful).
      const { root: mirrorRoot } = await getNotesMirrorRoot();

      const res = await authFetch(`${API_BASE}/upload_note`, {
        method: "POST",
        body: formData,
      });

      const result = await res.json().catch(() => ({}));
      const rel = sanitizeVirtualPath(result.path || "");
      if (rel && file) {
        registerLocalFile(rel, file);
        try {
          await mirrorUploadedBytes(file, rel, mirrorRoot);
        } catch (mirrorErr) {
          console.error("Local mirror write failed:", mirrorErr);
        }
      }

      if (res.ok) {
        onFileUploaded?.(result, file);
      } else {
        alert(`Error: ${apiErrorMessage(result, res)}`);
      }
    } catch (error) {
      console.error("Upload error:", error);
      alert(
        error?.message?.includes("fetch")
          ? "Upload failed: could not reach the backend. Is it running (e.g. http://localhost:8000)?"
          : `Upload failed: ${error?.message || "Network error"}`,
      );
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
        accept="image/*,.pdf,.txt,.md,.csv,.json"
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
        accept="image/*,.pdf,.txt,.md,.csv,.json"
        onChange={handleChange}
      />
    </div>
  );
});

export default FileUpload;
