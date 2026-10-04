import { useState } from "react";
import { getUserId } from "../auth";

function storageKey() {
  return `notescanner-tips-dismissed-${getUserId() || "user"}`;
}

function readDismissed() {
  try {
    return localStorage.getItem(storageKey()) === "1";
  } catch {
    return false;
  }
}

/** Dismissible three-step card for signed-in users: upload → ask → flashcards and review. */
export default function GettingStartedTips() {
  const [dismissed, setDismissed] = useState(readDismissed);
  if (dismissed) return null;

  const dismiss = () => {
    try {
      localStorage.setItem(storageKey(), "1");
    } catch {
      /* ignore */
    }
    setDismissed(true);
  };

  return (
    <section className="tips-card" aria-label="Getting started">
      <ol className="tips-steps">
        <li className="tips-step">
          <span className="tips-step-num">1</span>
          <span>
            <strong>Upload notes</strong> with the file button in the explorer, or drag files onto a folder.
          </span>
        </li>
        <li className="tips-step">
          <span className="tips-step-num">2</span>
          <span>
            <strong>Ask questions</strong> in the chat. Try the sample notes in “Getting started”.
          </span>
        </li>
        <li className="tips-step">
          <span className="tips-step-num">3</span>
          <span>
            <strong>Make flashcards</strong>, save them as a deck, and review what's due each day.
          </span>
        </li>
      </ol>
      <button type="button" className="tips-dismiss" onClick={dismiss} aria-label="Hide tips" title="Hide tips">
        ×
      </button>
    </section>
  );
}
