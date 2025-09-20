import { useState } from "react";

export default function FileUpload({ path, onFileUploaded }) {
  const [file, setFile] = useState(null);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!file) return;

    const formData = new FormData();
    formData.append("path", path);
    formData.append("file", file);
    formData.append("auto_extract", "true"); // Enable automatic text extraction and embedding

    try {
      const res = await fetch("http://localhost:8000/upload_note", {
        method: "POST",
        body: formData,
      });

      if (res.ok) {
        const result = await res.json();
        console.log("✅ File processed:", result);
        setFile(null);
        onFileUploaded();
        alert(`File uploaded and processed! Created ${result.chunks_created} chunks.`);
      } else {
        const error = await res.json();
        alert(`Error: ${error.detail || 'Upload failed'}`);
      }
    } catch (error) {
      console.error("Upload error:", error);
      alert("Upload failed. Please try again.");
    }
  };

  return (
    <div className="folder-form">
      <input
        type="file"
        accept="image/*,.pdf"
        onChange={(e) => setFile(e.target.files[0])}
      />
      <button type="submit" onClick={handleSubmit}>+ File</button>
    </div>
  );
}