"""Temporary per-chat uploads (the + button in chat), kept only in this process's memory."""
import threading
import time

from fastapi import HTTPException

from backend.settings import CHAT_CACHE_TTL_SECONDS, MAX_CHAT_FILES_PER_SESSION

_cache: dict[str, dict] = {}
_lock = threading.Lock()


def _key(user_id: str, chat_session_id: str) -> str:
    return f"{user_id}::{(chat_session_id or '').strip()}"


def _prune_locked(now: float) -> None:
    """Drop chat sessions nobody has used for CHAT_CACHE_TTL_SECONDS (e.g. the tab was closed)."""
    expired = [k for k, v in _cache.items() if now - v["last_used"] > CHAT_CACHE_TTL_SECONDS]
    for k in expired:
        del _cache[k]


def count(user_id: str, chat_session_id: str) -> int:
    with _lock:
        entry = _cache.get(_key(user_id, chat_session_id))
        return len(entry["items"]) if entry else 0


def add(user_id: str, chat_session_id: str, item: dict) -> int:
    now = time.time()
    with _lock:
        _prune_locked(now)
        entry = _cache.setdefault(_key(user_id, chat_session_id), {"items": [], "last_used": now})
        if len(entry["items"]) >= MAX_CHAT_FILES_PER_SESSION:
            raise HTTPException(
                status_code=400,
                detail=f"This chat already has {MAX_CHAT_FILES_PER_SESSION} files. Start a new chat to add more.",
            )
        entry["items"].append(item)
        entry["last_used"] = now
        return len(entry["items"])


def items(user_id: str, chat_session_id: str) -> list[dict]:
    if not (chat_session_id or "").strip():
        return []
    now = time.time()
    with _lock:
        _prune_locked(now)
        entry = _cache.get(_key(user_id, chat_session_id))
        if not entry:
            return []
        entry["last_used"] = now
        return list(entry["items"])


def clear(user_id: str, chat_session_id: str) -> int:
    with _lock:
        entry = _cache.pop(_key(user_id, chat_session_id), None)
        return len(entry["items"]) if entry else 0


def clear_user(user_id: str) -> None:
    prefix = f"{user_id}::"
    with _lock:
        for k in [k for k in _cache if k.startswith(prefix)]:
            del _cache[k]
