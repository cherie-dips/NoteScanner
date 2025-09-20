import { useState } from "react";

export default function FolderForm({ path, onFolderCreated }) {
  const [name, setName] = useState("");

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!name) return;

    const formData = new FormData();
    formData.append("path", path);
    formData.append("name", name);

    const res = await fetch("http://localhost:8000/create_folder", {
      method: "POST",
      body: formData,
    });

    if (res.ok) {
      setName("");
      onFolderCreated();
    }
  };

  return (
    <div className="folder-form">
      <input
        type="text"
        placeholder="Folder name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        required
      />
      <button type="submit" onClick={handleSubmit}>+ Folder</button>
    </div>
  );
}