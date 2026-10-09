"""Sign-up, sign-in, sign-out, password changes/resets and account deletion."""
import logging
import re
import secrets
import time
import uuid

import httpx
from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse

from backend import (
    chat_cache,
    feedback,
    legacy_migration,
    mailer,
    preferences,
    rate_limit,
    settings,
    starter_notes,
    study_store,
    uploads,
    usage,
)
from backend.auth import (
    SESSION_HEADER,
    create_session,
    end_session,
    get_user_id_from_session,
    hash_password,
    verify_password,
)
from backend.chroma_store import (
    delete_user_notes_collection,
    delete_user_vfs_collection,
    onenote_token_delete,
    password_reset_create,
    password_reset_delete_for_user,
    password_reset_pop_user_id,
    session_delete_all_for_user,
    user_create,
    user_delete,
    user_document_delete_many,
    user_exists_by_email,
    user_get_by_email,
    user_get_by_id,
    user_update_password,
)
from backend.deps import SignedInUser
from backend.rate_limit import client_ip
from backend.settings import (
    FRONTEND_URL,
    LIMIT_FORGOT_PER_EMAIL,
    LIMIT_FORGOT_PER_IP,
    LIMIT_GUEST_ID_PER_IP,
    LIMIT_LOGIN_PER_EMAIL,
    LIMIT_LOGIN_PER_IP,
    LIMIT_PASSWORD_CHECK_PER_USER,
    LIMIT_REGISTER_PER_IP,
    LIMIT_RESET_PER_IP,
    MAX_PASSWORD_BYTES,
    MIN_PASSWORD_LEN,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _error(message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status_code)


def _password_problem(password: str) -> str | None:
    if len(password) < MIN_PASSWORD_LEN:
        return f"Password must be at least {MIN_PASSWORD_LEN} characters."
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        return "Password is too long (maximum 72 characters)."
    return None


def _database_error(action: str, e: Exception) -> JSONResponse:
    """Log the real cause; tell the user whether the database is down or something else failed."""
    logger.exception("%s failed", action)
    if isinstance(e, (ConnectionError, TimeoutError, httpx.TransportError)) or "connect" in str(e).lower():
        return _error("Can't reach the database right now. Please try again in a moment.", 503)
    return _error(f"{action} failed because of a server error. Please try again.", 500)


def _check_password(user_id: str, password: str) -> bool:
    user = user_get_by_id(user_id, with_password=True)
    return bool(user and user.get("hashed_password") and verify_password(password, user["hashed_password"]))


@router.post("/register")
def register(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    name: str = Form(""),
    accepted_privacy: str = Form(""),
    app_name: str = Form(""),
):
    rate_limit.check("register", client_ip(request), *LIMIT_REGISTER_PER_IP)
    if accepted_privacy.strip().lower() not in ("1", "true", "yes", "on"):
        return _error("Please read and accept the privacy note.")
    email = (email or "").strip().lower()
    name = (name or "").strip()[:80]
    if len(email) > 254 or not _EMAIL_RE.match(email):
        return _error("Please enter a valid email address.")
    problem = _password_problem(password)
    if problem:
        return _error(problem)
    try:
        if user_exists_by_email(email):
            return _error("Email already registered.")
        display_name = name or email.split("@")[0]
        user_id = user_create(
            email,
            hash_password(password),
            display_name,
            privacy_accepted_at=int(time.time()),
            privacy_version=settings.PRIVACY_VERSION,
        )
        session_id = create_session(user_id)
        if settings.STARTER_NOTES:
            starter_notes.add_starter_notes_in_background(user_id, (app_name or "").strip())
        return JSONResponse({"session_id": session_id, "user_id": user_id, "name": display_name})
    except Exception as e:
        return _database_error("Sign-up", e)


@router.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...)):
    ip = client_ip(request)
    email = (email or "").strip().lower()
    rate_limit.check("login-ip", ip, *LIMIT_LOGIN_PER_IP)
    rate_limit.check("login", f"{ip}|{email}", *LIMIT_LOGIN_PER_EMAIL)
    try:
        user = user_get_by_email(email)
        if not user or not verify_password(password, user["hashed_password"]):
            return _error("Invalid email or password.", 401)
        user_id = user["user_id"]
        session_id = create_session(user_id)
        name = user.get("name") or (user.get("email") or "").split("@")[0]
        return JSONResponse({"session_id": session_id, "user_id": user_id, "name": name})
    except Exception as e:
        return _database_error("Sign-in", e)


@router.post("/logout")
def logout(request: Request):
    """End the session on the server so the session id stops working everywhere."""
    end_session(request.headers.get(SESSION_HEADER))
    return JSONResponse({"message": "Signed out."})


@router.get("/me")
def get_me(request: Request):
    """Return current user info (name) for the session. Requires valid X-Session-Id."""
    user_id = get_user_id_from_session(request.headers.get(SESSION_HEADER))
    if not user_id:
        return _error("Not signed in.", 401)
    user = user_get_by_id(user_id)
    if not user:
        return _error("User not found.", 404)
    name = user.get("name") or (user.get("email") or "").split("@")[0]
    return JSONResponse({
        "name": name or "User",
        "user_id": user_id,
        "email": user.get("email") or "",
        "is_admin": (user.get("email") or "").lower() in settings.ADMIN_EMAILS,
        "preferences": preferences.get(user_id),
    })


@router.get("/account/preferences")
def get_preferences(user_id: SignedInUser = None):
    return preferences.public(user_id)


@router.post("/account/preferences")
def set_preferences(reminders: str = Form(None), answer_language: str = Form(None), user_id: SignedInUser = None):
    try:
        preferences.update(
            user_id,
            reminders=None if reminders is None else reminders.strip().lower() in ("1", "true", "yes", "on"),
            answer_language=answer_language,
        )
    except ValueError as e:
        return _error(str(e))
    return preferences.public(user_id)


@router.get("/guest_id")
def get_guest_id(request: Request):
    """Return a new guest id for anonymous use. Frontend stores it and sends X-Guest-Id header."""
    rate_limit.check("guest-id", client_ip(request), *LIMIT_GUEST_ID_PER_IP)
    return JSONResponse({"guest_id": str(uuid.uuid4())})


@router.post("/account/change_password")
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    user_id: SignedInUser = None,
):
    rate_limit.check("password-check", user_id, *LIMIT_PASSWORD_CHECK_PER_USER)
    if not _check_password(user_id, current_password):
        return _error("Your current password is incorrect.", 400)
    problem = _password_problem(new_password)
    if problem:
        return _error(problem)
    user_update_password(user_id, hash_password(new_password))
    # Sign out every other device; this one stays signed in.
    session_delete_all_for_user(user_id, keep_session_id=request.headers.get(SESSION_HEADER))
    return JSONResponse({"message": "Password changed. Other devices have been signed out."})


@router.post("/account/delete")
def delete_account(password: str = Form(...), user_id: SignedInUser = None):
    """Permanently delete the account and everything stored for it."""
    rate_limit.check("password-check", user_id, *LIMIT_PASSWORD_CHECK_PER_USER)
    if not _check_password(user_id, password):
        return _error("Password is incorrect.", 400)
    delete_user_notes_collection(user_id)
    delete_user_vfs_collection(user_id)
    study_store.delete_all(user_id)
    user_document_delete_many(user_id, path_filter=None)
    onenote_token_delete(user_id)
    password_reset_delete_for_user(user_id)
    chat_cache.clear_user(user_id)
    uploads.forget_user_jobs(user_id)
    legacy_migration.forget_user(user_id)
    usage.delete_user(user_id)
    feedback.delete_user(user_id)
    preferences.forget(user_id)
    session_delete_all_for_user(user_id)
    user_delete(user_id)
    return JSONResponse({"message": "Your account and all your notes have been deleted."})


FORGOT_MESSAGE = "If an account exists for that email, we've sent a link to reset the password. It expires in 1 hour."


@router.post("/password/forgot")
def forgot_password(request: Request, email: str = Form(...)):
    if not mailer.is_configured():
        return _error("Password reset by email isn't set up on this server.", 503)
    email = (email or "").strip().lower()
    rate_limit.check("forgot-ip", client_ip(request), *LIMIT_FORGOT_PER_IP)
    rate_limit.check("forgot", email, *LIMIT_FORGOT_PER_EMAIL)
    user = user_get_by_email(email) if _EMAIL_RE.match(email) else None
    if user:
        token = secrets.token_urlsafe(32)
        password_reset_create(user["user_id"], token)
        link = f"{FRONTEND_URL.rstrip('/')}/?reset_token={token}"
        try:
            mailer.send_password_reset(user["email"], link)
        except Exception:
            logger.exception("Password reset email failed")
    # Same answer either way, so the form can't be used to find out who has an account.
    return JSONResponse({"message": FORGOT_MESSAGE})


@router.post("/password/reset")
def reset_password(request: Request, token: str = Form(...), new_password: str = Form(...)):
    rate_limit.check("reset", client_ip(request), *LIMIT_RESET_PER_IP)
    problem = _password_problem(new_password)
    if problem:
        return _error(problem)
    user_id = password_reset_pop_user_id((token or "").strip())
    if not user_id:
        return _error("This reset link is invalid or has expired. Please ask for a new one.")
    user_update_password(user_id, hash_password(new_password))
    session_delete_all_for_user(user_id)
    return JSONResponse({"message": "Password updated. Please sign in with your new password."})
