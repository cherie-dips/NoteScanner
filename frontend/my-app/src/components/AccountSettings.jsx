import { useEffect, useState } from "react";
import { apiRequest, errorText } from "../auth";
import { APP_NAME } from "../embed";

const MIN_PASSWORD = 8;
const MAX_PASSWORD = 72;

/** Account settings modal: preferences, change password, delete all notes, delete account. */
export default function AccountSettings({ onClose, onNotesDeleted, onAccountDeleted }) {
  const [prefs, setPrefs] = useState(null); // { reminders, answer_language, languages, reminders_available }
  const [prefsStatus, setPrefsStatus] = useState(null);

  const [currentPw, setCurrentPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [pwStatus, setPwStatus] = useState(null);
  const [pwBusy, setPwBusy] = useState(false);

  const [deleteNotesText, setDeleteNotesText] = useState("");
  const [notesStatus, setNotesStatus] = useState(null);
  const [notesBusy, setNotesBusy] = useState(false);

  const [deletePw, setDeletePw] = useState("");
  const [accountStatus, setAccountStatus] = useState(null);
  const [accountBusy, setAccountBusy] = useState(false);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") onClose?.();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    let cancelled = false;
    apiRequest("/account/preferences")
      .then((data) => {
        if (!cancelled) setPrefs(data);
      })
      .catch((err) => {
        if (!cancelled) setPrefsStatus({ type: "error", text: `Couldn't load preferences. ${errorText(err)}` });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Show the change right away; undo it if the server refuses.
  const savePrefs = async (patch) => {
    const before = prefs;
    setPrefs((p) => ({ ...p, ...patch }));
    setPrefsStatus(null);
    const form = {};
    if ("reminders" in patch) form.reminders = patch.reminders ? "true" : "false";
    if ("answer_language" in patch) form.answer_language = patch.answer_language;
    try {
      const data = await apiRequest("/account/preferences", { method: "POST", form });
      setPrefs(data);
      setPrefsStatus({ type: "success", text: "Saved." });
    } catch (err) {
      setPrefs(before);
      setPrefsStatus({ type: "error", text: errorText(err) });
    }
  };

  const languages = prefs?.languages?.length ? prefs.languages : ["English"];

  const changePassword = async (e) => {
    e.preventDefault();
    if (newPw.length < MIN_PASSWORD || newPw.length > MAX_PASSWORD) {
      setPwStatus({ type: "error", text: `New password must be ${MIN_PASSWORD}–${MAX_PASSWORD} characters.` });
      return;
    }
    if (newPw !== confirmPw) {
      setPwStatus({ type: "error", text: "The new passwords don't match." });
      return;
    }
    setPwBusy(true);
    setPwStatus(null);
    try {
      const data = await apiRequest("/account/change_password", {
        method: "POST",
        form: { current_password: currentPw, new_password: newPw },
        keepSessionOn401: true, // a wrong current password must not sign the user out
      });
      setPwStatus({ type: "success", text: data.message || "Password changed." });
      setCurrentPw("");
      setNewPw("");
      setConfirmPw("");
    } catch (err) {
      setPwStatus({ type: "error", text: errorText(err) });
    } finally {
      setPwBusy(false);
    }
  };

  const deleteAllNotes = async (e) => {
    e.preventDefault();
    if (deleteNotesText !== "DELETE") return;
    setNotesBusy(true);
    setNotesStatus(null);
    try {
      const data = await apiRequest("/notes", { method: "DELETE" });
      setNotesStatus({ type: "success", text: data.message || "All notes deleted." });
      setDeleteNotesText("");
      onNotesDeleted?.();
    } catch (err) {
      setNotesStatus({ type: "error", text: errorText(err) });
    } finally {
      setNotesBusy(false);
    }
  };

  const deleteAccount = async (e) => {
    e.preventDefault();
    if (!deletePw) return;
    if (!window.confirm("Delete your account and all your notes? This can't be undone.")) return;
    setAccountBusy(true);
    setAccountStatus(null);
    try {
      await apiRequest("/account/delete", {
        method: "POST",
        form: { password: deletePw },
        keepSessionOn401: true,
      });
      onAccountDeleted?.();
    } catch (err) {
      setAccountStatus({ type: "error", text: errorText(err) });
      setAccountBusy(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-card account-settings"
        role="dialog"
        aria-modal="true"
        aria-labelledby="account-settings-title"
        onClick={(e) => e.stopPropagation()}
      >
        <button type="button" className="auth-close" onClick={onClose} aria-label="Close">
          ×
        </button>
        <h2 id="account-settings-title" className="modal-title">Account settings</h2>

        <section className="settings-section account-preferences">
          <h3 className="settings-heading">Preferences</h3>
          <label className="settings-row">
            <input
              type="checkbox"
              className="prefs-reminders"
              checked={!!prefs?.reminders}
              disabled={!prefs || !prefs.reminders_available}
              onChange={(e) => savePrefs({ reminders: e.target.checked })}
            />
            <span>Daily review reminder email (when flashcards are due)</span>
          </label>
          {prefs && !prefs.reminders_available && (
            <p className="settings-text prefs-reminders-note">
              Reminder emails aren't set up on this server yet.
            </p>
          )}
          <label className="settings-row settings-row--select">
            <span>Answer language</span>
            <select
              className="prefs-language"
              value={prefs?.answer_language || "English"}
              disabled={!prefs}
              onChange={(e) => savePrefs({ answer_language: e.target.value })}
            >
              {languages.map((lang) => (
                <option key={lang} value={lang}>
                  {lang}
                </option>
              ))}
            </select>
          </label>
          <p className="settings-text">
            Answers, flashcards, quizzes and summaries are written in this language. Formulas and technical
            terms stay as they are.
          </p>
          {prefsStatus && (
            <p className={`settings-status settings-status--${prefsStatus.type} prefs-status`}>{prefsStatus.text}</p>
          )}
        </section>

        <section className="settings-section">
          <h3 className="settings-heading">Change password</h3>
          <form className="auth-form account-change-password" onSubmit={changePassword}>
            <input
              type="password"
              className="auth-input"
              placeholder="Current password"
              aria-label="Current password"
              autoComplete="current-password"
              value={currentPw}
              onChange={(e) => setCurrentPw(e.target.value)}
              required
            />
            <input
              type="password"
              className="auth-input"
              placeholder={`New password (at least ${MIN_PASSWORD} characters)`}
              aria-label="New password"
              autoComplete="new-password"
              minLength={MIN_PASSWORD}
              maxLength={MAX_PASSWORD}
              value={newPw}
              onChange={(e) => setNewPw(e.target.value)}
              required
            />
            <input
              type="password"
              className="auth-input"
              placeholder="Repeat new password"
              aria-label="Repeat new password"
              autoComplete="new-password"
              value={confirmPw}
              onChange={(e) => setConfirmPw(e.target.value)}
              required
            />
            {pwStatus && <p className={`settings-status settings-status--${pwStatus.type}`}>{pwStatus.text}</p>}
            <button type="submit" className="auth-btn" disabled={pwBusy}>
              {pwBusy ? "Saving…" : "Change password"}
            </button>
          </form>
        </section>

        <section className="settings-section settings-section--danger">
          <h3 className="settings-heading">Delete all my notes</h3>
          <p className="settings-text">
            Removes every folder, file, and extracted text from {APP_NAME}. Original files saved on this
            computer are not touched. Type <strong>DELETE</strong> to confirm.
          </p>
          <form className="auth-form account-delete-notes" onSubmit={deleteAllNotes}>
            <input
              type="text"
              className="auth-input"
              placeholder="Type DELETE"
              aria-label="Type DELETE to confirm"
              value={deleteNotesText}
              onChange={(e) => setDeleteNotesText(e.target.value)}
            />
            {notesStatus && <p className={`settings-status settings-status--${notesStatus.type}`}>{notesStatus.text}</p>}
            <button
              type="submit"
              className="auth-btn settings-danger-btn"
              disabled={notesBusy || deleteNotesText !== "DELETE"}
            >
              {notesBusy ? "Deleting…" : "Delete all notes"}
            </button>
          </form>
        </section>

        <section className="settings-section settings-section--danger">
          <h3 className="settings-heading">Delete account</h3>
          <p className="settings-text">
            Permanently deletes your account, notes, and study decks. Enter your password to confirm.
          </p>
          <form className="auth-form account-delete" onSubmit={deleteAccount}>
            <input
              type="password"
              className="auth-input"
              placeholder="Password"
              aria-label="Password to delete account"
              autoComplete="current-password"
              value={deletePw}
              onChange={(e) => setDeletePw(e.target.value)}
              required
            />
            {accountStatus && (
              <p className={`settings-status settings-status--${accountStatus.type}`}>{accountStatus.text}</p>
            )}
            <button type="submit" className="auth-btn settings-danger-btn" disabled={accountBusy || !deletePw}>
              {accountBusy ? "Deleting…" : "Delete my account"}
            </button>
          </form>
        </section>
      </div>
    </div>
  );
}
