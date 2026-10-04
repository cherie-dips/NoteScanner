"""
Daily "cards due" reminder emails for students who turned them on (Account settings).

A background thread checks every 10 minutes; once the local time (APP_TIMEZONE) is past
REMINDER_HOUR it emails each opted-in student who has flashcards due, at most once per day.
Requires SMTP (see mailer.py). Runs in the single server process.
"""
import datetime as dt
import logging
import threading
import time

from backend import alerts, mailer, settings, study_store, usage
from backend.chroma_store import user_update_metadata, users_where

logger = logging.getLogger(__name__)

CHECK_EVERY_SECONDS = 600
_started = False
_start_lock = threading.Lock()


def _end_of_day(day: dt.date) -> int:
    return int(dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), tzinfo=usage.tz()).timestamp())


def reminder_text(name: str, due: int) -> str:
    cards = "1 flashcard is" if due == 1 else f"{due} flashcards are"
    return (
        f"Hi {name or 'there'},\n\n"
        f"{cards} due for review today in NoteScanner. A few minutes now saves a lot of re-reading later.\n\n"
        f"Open NoteScanner: {settings.FRONTEND_URL}\n\n"
        "You get this email because daily reminders are on. You can turn them off in Account settings."
    )


def send_due_reminders(now: float | None = None) -> int:
    """Send today's reminders (if it's past the reminder hour). Returns how many were sent."""
    if not (settings.REMINDERS_ENABLED and mailer.is_configured()):
        return 0
    now = time.time() if now is None else now
    local = dt.datetime.fromtimestamp(now, usage.tz())
    if local.hour < settings.REMINDER_HOUR:
        return 0
    today = local.date().isoformat()
    sent = 0
    for user in users_where({"pref_reminders": 1}):
        meta = user["meta"]
        if meta.get("reminder_sent_day") == today or not user.get("email"):
            continue
        try:
            due = study_store.due_count(user["user_id"], until=_end_of_day(local.date()))
            if due > 0:
                mailer.send_email(user["email"], f"{due} flashcards to review today", reminder_text(user.get("name"), due))
                sent += 1
            # Mark the day as handled either way, so each student is checked once per day.
            user_update_metadata(user["user_id"], reminder_sent_day=today)
        except Exception as e:
            logger.exception("Reminder for %s failed", user["user_id"])
            alerts.notify("reminder-failed", f"Reminder email failed: {type(e).__name__}: {e}")
    return sent


def _loop() -> None:
    while True:
        try:
            n = send_due_reminders()
            if n:
                logger.info("Sent %d review reminders", n)
        except Exception:
            logger.exception("Reminder check failed")
        time.sleep(CHECK_EVERY_SECONDS)


def start() -> None:
    """Start the reminder thread once (no-op when reminders or email aren't set up)."""
    global _started
    with _start_lock:
        if _started or not (settings.REMINDERS_ENABLED and mailer.is_configured()):
            return
        _started = True
    threading.Thread(target=_loop, name="reminders", daemon=True).start()
