"""Simple session-based auth (no JWT) and password hashing for NoteScanner."""
import logging
import uuid

from fastapi import HTTPException, Request

from backend.chroma_store import session_create, session_delete, session_get_user_id

# Re-export for callers that import from auth
from passlib.context import CryptContext

logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

SESSION_HEADER = "X-Session-Id"
GUEST_HEADER = "X-Guest-Id"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_session(user_id: str) -> str:
    return session_create(user_id)


def end_session(session_id: str | None) -> None:
    session_delete(session_id or "")


def get_user_id_from_session(session_id: str) -> str | None:
    """User id for a live session. Raises HTTP 503 if the database can't be reached."""
    try:
        return session_get_user_id(session_id)
    except Exception:
        logger.exception("Session lookup failed")
        raise HTTPException(
            status_code=503,
            detail="Can't reach the database right now. Please try again in a moment.",
        )


def _valid_guest_id(guest_id: str) -> bool:
    try:
        uuid.UUID(guest_id)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _signed_in_user_or_none(request: Request) -> str | None:
    """
    User id for the X-Session-Id header, None if no header was sent.
    A header that no longer matches a live session raises HTTP 401 so the app can sign out.
    """
    session_id = (request.headers.get(SESSION_HEADER) or "").strip()
    if not session_id:
        return None
    user_id = get_user_id_from_session(session_id)
    if not user_id:
        raise HTTPException(status_code=401, detail="Your session has expired. Please sign in again.")
    return user_id


def get_effective_user(request: Request) -> tuple[str, bool]:
    """
    Returns (effective_user_id, is_guest).
    - If X-Session-Id is sent -> (user_id, False), or HTTP 401 if that session is no longer valid.
    - Else if X-Guest-Id is a valid id -> ("guest_<id>", True).
    - Else raises ValueError (caller should return 400; client must call GET /guest_id first).
    """
    user_id = _signed_in_user_or_none(request)
    if user_id:
        return user_id, False
    guest_id = (request.headers.get(GUEST_HEADER) or "").strip()
    if guest_id and _valid_guest_id(guest_id):
        return f"guest_{guest_id}", True
    raise ValueError("Missing X-Guest-Id. Call GET /guest_id first or sign in.")


def require_signed_in_user(request: Request) -> str:
    """User id for routes that store data or call paid AI services; HTTP 401 for guests."""
    user_id = _signed_in_user_or_none(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Please sign in to continue.")
    return user_id
