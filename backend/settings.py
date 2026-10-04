"""Settings read from the environment, plus request limits and thresholds in one place."""
import os


def _env_str(name: str, default: str) -> str:
    return (os.getenv(name) or default).strip() or default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env_str(name, str(default)))
    except ValueError:
        return default


MAX_UPLOAD_MB = _env_float("MAX_UPLOAD_MB", 20)
MAX_UPLOAD_BYTES = int(MAX_UPLOAD_MB * 1024 * 1024)
MAX_ACTIVE_UPLOADS_PER_USER = 10  # queued + processing; each holds the file in memory until done
CHAT_CACHE_TTL_SECONDS = int(_env_float("CHAT_CACHE_TTL_SECONDS", 7200))
MAX_CHAT_UPLOAD_CHARS = int(_env_float("MAX_CHAT_UPLOAD_CHARS", 300000))
MAX_CHAT_FILES_PER_SESSION = 10
MAX_EDITED_TEXT_CHARS = 500000

# Minimum cosine similarity for a source to count as relevant. Every stage uses the same 0..1 scale.
MIN_SCORE_CHAT_UPLOAD = 0.30
MIN_SCORE_OPEN_FILE = 0.20
MIN_SCORE_COURSE = 0.25
MIN_SCORE_ALL_NOTES = 0.25
MAIN_SOURCE_BOOST = 0.03  # small ranking boost for files the student marked as their main source
SOURCE_SCORE_WINDOW = 0.2  # keep excerpts scoring within this much of the best one (drops loosely related files)

# Request limits as (max requests, window seconds). Per-IP limits are generous because a whole
# campus can share one public IP; the per-email login limit is what stops password guessing.
LIMIT_REGISTER_PER_IP = (50, 3600)
LIMIT_LOGIN_PER_IP = (200, 900)
LIMIT_LOGIN_PER_EMAIL = (10, 900)
LIMIT_PASSWORD_CHECK_PER_USER = (10, 900)  # change password / delete account
LIMIT_FORGOT_PER_IP = (20, 3600)
LIMIT_FORGOT_PER_EMAIL = (3, 3600)
LIMIT_GUEST_ID_PER_IP = (100, 3600)
LIMIT_UPLOADS_PER_USER = (30, 600)
LIMIT_QUESTIONS_PER_USER = (60, 600)
LIMIT_STUDY_PER_USER = (20, 600)
LIMIT_ONENOTE_SYNC_PER_USER = (5, 600)
LIMIT_ONENOTE_CONNECT_PER_USER = (10, 600)
LIMIT_RESET_PER_IP = (20, 3600)            # using password-reset links
LIMIT_TREE_CHANGES_PER_USER = (120, 60)    # create, move, rename, delete, main-source marking
LIMIT_TEXT_EDITS_PER_USER = (60, 600)      # saving edited text (re-processes the file for search)
LIMIT_DECK_CHANGES_PER_USER = (60, 600)    # creating and deleting flashcard decks
LIMIT_DELETE_ALL_PER_USER = (5, 3600)      # "delete all my notes"

# Overall caps on every request (checked before any route), on top of the per-route limits above.
LIMIT_ALL_PER_IP = (1200, 60)      # one network address (a campus may share one)
LIMIT_ALL_PER_SESSION = (300, 60)  # one signed-in browser
UNLIMITED_PATHS = ("/health",)     # monitoring checks are never limited

# Daily caps on paid AI use per user (cost control): uploads read by Sarvam Vision, questions and
# study tools answered by Sarvam chat models.
LIMIT_UPLOADS_PER_DAY = (200, 86400)
LIMIT_QUESTIONS_PER_DAY = (300, 86400)
LIMIT_STUDY_PER_DAY = (100, 86400)

MIN_PASSWORD_LEN = 8
MAX_PASSWORD_BYTES = 72  # bcrypt ignores anything after 72 bytes

FRONTEND_URL = _env_str("FRONTEND_URL", "https://cherie-dips.github.io/NoteScanner/")

DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "https://cherie-dips.github.io",
)


def allowed_origins() -> list[str]:
    """Websites allowed to call this API (CORS_ORIGINS env, comma-separated)."""
    raw = (os.getenv("CORS_ORIGINS") or "").strip()
    if not raw:
        return list(DEFAULT_CORS_ORIGINS)
    return [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]


def _env_bool(name: str, default: bool) -> bool:
    return _env_str(name, "true" if default else "false").lower() in ("1", "true", "yes", "on")


# ---------- Running a pilot (ROADMAP.md steps 0–2) ----------
APP_TIMEZONE = _env_str("APP_TIMEZONE", "Asia/Kolkata")  # "today", streaks and reminder times use this
STARTER_NOTES = _env_bool("STARTER_NOTES", True)          # new accounts get a "Getting started" folder
PRIVACY_VERSION = "2026-10"                               # bump when the privacy note changes
ADMIN_EMAILS = {e.strip().lower() for e in (os.getenv("ADMIN_EMAILS") or "").split(",") if e.strip()}
ALERT_WEBHOOK_URL = (os.getenv("ALERT_WEBHOOK_URL") or "").strip()  # Slack/Discord-style webhook for errors

# AI cost estimate. Prices are 0 (unknown) until you set them from your Sarvam plan.
AI_PRICE_PER_PAGE = _env_float("AI_PRICE_PER_PAGE", 0)                  # Sarvam Vision, per page read
AI_PRICE_PER_1K_INPUT_TOKENS = _env_float("AI_PRICE_PER_1K_INPUT_TOKENS", 0)
AI_PRICE_PER_1K_OUTPUT_TOKENS = _env_float("AI_PRICE_PER_1K_OUTPUT_TOKENS", 0)
AI_PRICE_CURRENCY = _env_str("AI_PRICE_CURRENCY", "INR")
AI_MONTHLY_BUDGET = _env_float("AI_MONTHLY_BUDGET", 0)  # 0 = no budget; otherwise paid AI pauses when reached

# Daily review reminder emails (only when SMTP is set up and the student turned them on).
REMINDERS_ENABLED = _env_bool("REMINDERS_ENABLED", True)
REMINDER_HOUR = int(_env_float("REMINDER_HOUR", 8))  # local hour (APP_TIMEZONE) after which reminders go out

ANSWER_LANGUAGES = (
    "English", "Hindi", "Bengali", "Gujarati", "Kannada", "Malayalam",
    "Marathi", "Odia", "Punjabi", "Tamil", "Telugu",
)
