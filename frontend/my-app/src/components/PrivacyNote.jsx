import { useEffect } from "react";
import { APP_NAME, EMBEDDED } from "../embed";

/*
 * DRAFT privacy note. The project owner must review this text with the college (data office / IT)
 * before inviting students, and fill in the contact line. Keep it in plain language.
 */
const CONTACT_LINE = "Questions or requests: [add a contact email before launch].";

/** Plain-language privacy note, shown as a modal (sign-up, signed-out page, profile menu). */
export default function PrivacyNote({ onClose }) {
  useEffect(() => {
    // Capture phase so Escape closes only this note, not the sign-up box underneath it.
    const onKey = (e) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose?.();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  return (
    <div className="modal-overlay privacy-overlay" onClick={onClose}>
      <div
        className="modal-card privacy-note"
        role="dialog"
        aria-modal="true"
        aria-labelledby="privacy-note-title"
        onClick={(e) => e.stopPropagation()}
      >
        <button type="button" className="auth-close privacy-close" onClick={onClose} aria-label="Close">
          ×
        </button>
        <h2 id="privacy-note-title" className="modal-title">Privacy note</h2>
        <div className="privacy-body">
          <h3>What {APP_NAME} stores</h3>
          <ul>
            <li>Your account: email address, name, and your password in scrambled form (a hash, not the password itself).</li>
            <li>The text {APP_NAME} reads from your notes, and search data made from that text.</li>
            <li>Your flashcard decks and review history, quiz results, and any ratings or feedback you send.</li>
            <li>Simple usage counts (for example how many questions you asked today), used to keep the service running and within budget.</li>
          </ul>

          <h3>Where it is kept and who handles it</h3>
          <ul>
            <li>Notes, text and study data are stored in a Chroma Cloud database.</li>
            <li>To read PDFs and photos and to write answers, flashcards and quizzes, your note text and questions are sent to Sarvam AI.</li>
            <li>The {APP_NAME} server runs on Hugging Face hosting.</li>
            {EMBEDDED && (
              <li>
                Ask AI in the Notes tab uses the same account. Questions asked there, and the course notes
                they are about, are handled in the same way.
              </li>
            )}
            <li>Your original PDFs and images are not uploaded for storage: they stay on your device. Only the text read from them is kept.</li>
          </ul>

          <h3>Things to keep in mind</h3>
          <ul>
            <li>Answers are written by AI and can be wrong. Check important facts against your notes and textbooks.</li>
            <li>Don't upload anything you aren't allowed to share, or personal information about other people.</li>
          </ul>

          <h3>Deleting your data</h3>
          <p>
            Account settings → <strong>Delete account</strong> permanently removes your account, notes, text, decks
            and study history. You can also delete all notes and keep your account.
          </p>

          <p className="privacy-contact">{CONTACT_LINE}</p>
        </div>
        <button type="button" className="auth-btn privacy-ok" onClick={onClose}>
          Got it
        </button>
      </div>
    </div>
  );
}
