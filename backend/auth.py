"""Simple session-based auth (no JWT) and password hashing for NoteScanner."""
from fastapi import Request

from backend.chroma_store import session_create, session_get_user_id

# Re-export for callers that import from auth
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

SESSION_HEADER = "X-Session-Id"
GUEST_HEADER = "X-Guest-Id"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_session(user_id: str) -> str:
    return session_create(user_id)


def get_user_id_from_session(session_id: str) -> str | None:
    return session_get_user_id(session_id)


def get_effective_user(request: Request) -> tuple[str, bool]:
    """
    Returns (effective_user_id, is_guest).
    - If X-Session-Id is valid -> (user_id, False).
    - Else if X-Guest-Id present -> ("guest_<id>", True).
    - Else raises ValueError (caller should return 400; client must call GET /guest_id first).
    """
    session_id = request.headers.get(SESSION_HEADER)
    user_id = get_user_id_from_session(session_id)
    if user_id:
        return user_id, False
    guest_id = request.headers.get(GUEST_HEADER)
    if guest_id:
        return f"guest_{guest_id}", True
    raise ValueError("Missing X-Guest-Id. Call GET /guest_id first or sign in.")
