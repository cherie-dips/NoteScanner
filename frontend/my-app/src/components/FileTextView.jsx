import { useCallback, useEffect, useState } from "react";
import { API_BASE } from "../config";
import { authFetch, apiErrorMessage, errorText } from "../auth";

const MAX_TEXT_CHARS = 500000;

/**
 * The text NoteScanner read from a file (what search, chat and study tools use).
 * Users can fix OCR mistakes or write notes into empty files; saving re-indexes the file.
 */
export default function FileTextView({ path }) {
  const [text, setText] = useState("");
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await authFetch(`${API_BASE}/file_text?path=${encodeURIComponent(path)}`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(apiErrorMessage(data, res));
      setText(data.text || "");
      setTruncated(!!data.truncated);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    setEditing(false);
    setNotice("");
    void load();
  }, [load]);

  const startEdit = () => {
    setDraft(text);
    setNotice("");
    setEditing(true);
  };

  const save = async () => {
    if (draft.length > MAX_TEXT_CHARS) {
      setError(`The text is too long (maximum ${MAX_TEXT_CHARS.toLocaleString()} characters).`);
      return;
    }
    setSaving(true);
    setError("");
    try {
      const fd = new FormData();
      fd.append("path", path);
      fd.append("text", draft);
      const res = await authFetch(`${API_BASE}/file_text`, { method: "POST", body: fd });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(apiErrorMessage(data, res));
      setEditing(false);
      setNotice(data.message || "Saved. Search and study tools now use the new text.");
      await load();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setSaving(false);
    }
  };

  if (loading && !editing) {
    return <div className="file-text-view file-text-view--loading">Loading text…</div>;
  }

  return (
    <div className="file-text-view">
      <div className="file-text-toolbar">
        <span className="file-text-hint">
          {editing
            ? "Fix mistakes or add notes, then save. Search, chat and study tools will use this text."
            : "Text NoteScanner read from this file."}
        </span>
        {editing ? (
          <>
            <button type="button" className="file-text-btn" onClick={() => setEditing(false)} disabled={saving}>
              Cancel
            </button>
            <button
              type="button"
              className="file-text-btn file-text-btn--primary file-text-save"
              onClick={save}
              disabled={saving}
            >
              {saving ? "Saving…" : "Save"}
            </button>
          </>
        ) : (
          <button
            type="button"
            className="file-text-btn file-text-edit"
            onClick={startEdit}
            disabled={(!!error && !text) || truncated}
            title={truncated ? "This text is too long to edit here." : undefined}
          >
            Edit
          </button>
        )}
      </div>
      {error && <div className="file-text-error" role="alert">{error}</div>}
      {notice && !error && <div className="file-text-notice">{notice}</div>}
      {truncated && !editing && (
        <div className="file-text-notice">
          Only the first part of a very long text is shown, so editing is turned off for this file.
        </div>
      )}
      {editing ? (
        <textarea
          className="file-text-editor"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          aria-label="Edit file text"
          spellCheck
          autoFocus
        />
      ) : (
        <pre className="preview-text file-text-content">
          {text || (error ? "" : "(No text yet. Click Edit to write notes here.)")}
        </pre>
      )}
    </div>
  );
}
