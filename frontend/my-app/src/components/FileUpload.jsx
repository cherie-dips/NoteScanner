import { forwardRef } from "react";

export const UPLOAD_ACCEPT = "image/*,.pdf,.txt,.md,.csv,.json,.log";

/**
 * Hidden multi-file picker. The chosen files are handed to `onFiles`; the upload queue
 * (useUploadQueue) sends them and tracks processing.
 */
const FileUpload = forwardRef(function FileUpload({ onFiles }, ref) {
  const handleChange = (e) => {
    const files = Array.from(e.target.files || []);
    e.target.value = "";
    if (files.length) onFiles?.(files);
  };

  return (
    <input
      ref={ref}
      type="file"
      multiple
      accept={UPLOAD_ACCEPT}
      onChange={handleChange}
      style={{ display: "none" }}
      aria-hidden
    />
  );
});

export default FileUpload;
