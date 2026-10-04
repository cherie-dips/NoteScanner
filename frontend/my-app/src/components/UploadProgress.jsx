const STATUS_LABELS = {
  uploading: "Uploading…",
  queued: "Waiting…",
  processing: "Reading text…",
  done: "Done",
  failed: "Failed",
};

/** "Uploads" list under the explorer toolbar: one row per file with its processing status. */
export default function UploadProgress({ items, onDismiss, onClearFinished, onMakeFlashcards }) {
  if (!items.length) return null;
  const finished = items.filter((it) => it.status === "done" || it.status === "failed").length;
  return (
    <div className="upload-progress" aria-live="polite">
      <div className="upload-progress-header">
        <span>Uploads</span>
        {finished > 0 && (
          <button type="button" className="upload-progress-clear" onClick={onClearFinished}>
            Clear finished
          </button>
        )}
      </div>
      <ul className="upload-progress-list">
        {items.map((it) => (
          <li key={it.key} className={`upload-item upload-item--${it.status}`}>
            <div className="upload-item-row">
              <span className="upload-item-name" title={it.path || it.name}>
                {it.name}
              </span>
              <span className={`upload-item-status upload-item-status--${it.status}`}>
                {STATUS_LABELS[it.status] || it.status}
              </span>
              {(it.status === "done" || it.status === "failed") && (
                <button
                  type="button"
                  className="upload-item-dismiss"
                  onClick={() => onDismiss(it.key)}
                  aria-label={`Dismiss ${it.name}`}
                  title="Dismiss"
                >
                  ×
                </button>
              )}
            </div>
            {it.error && <div className="upload-item-error">{it.error}</div>}
            {it.status === "done" && onMakeFlashcards && !it.deckStatus && it.path && (
              <button
                type="button"
                className="upload-item-flashcards"
                onClick={() => onMakeFlashcards(it)}
              >
                Make flashcards
              </button>
            )}
            {it.deckStatus === "making" && (
              <div className="upload-item-deck upload-item-deck--making">Making flashcards…</div>
            )}
            {(it.deckStatus === "done" || it.deckStatus === "error") && (
              <div className={`upload-item-deck upload-item-deck--${it.deckStatus}`}>{it.deckMessage}</div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
