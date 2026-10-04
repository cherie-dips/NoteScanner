"""Per-student preferences (reminder emails, answer language), kept in the user's record."""
import threading
import time

from backend import mailer
from backend.chroma_store import user_get_by_id, user_update_metadata
from backend.settings import ANSWER_LANGUAGES, REMINDERS_ENABLED

_CACHE_SECONDS = 60
_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


def _from_meta(meta: dict) -> dict:
    language = meta.get("pref_language") or "English"
    return {
        "reminders": bool(int(meta.get("pref_reminders") or 0)),
        "answer_language": language if language in ANSWER_LANGUAGES else "English",
    }


def reminders_available() -> bool:
    return REMINDERS_ENABLED and mailer.is_configured()


def get(user_id: str) -> dict:
    now = time.time()
    with _lock:
        hit = _cache.get(user_id)
        if hit and now - hit[0] < _CACHE_SECONDS:
            return dict(hit[1])
    user = user_get_by_id(user_id)
    prefs = _from_meta((user or {}).get("meta") or {})
    with _lock:
        _cache[user_id] = (now, prefs)
    return dict(prefs)


def answer_language(user_id: str) -> str:
    try:
        return get(user_id)["answer_language"]
    except Exception:
        return "English"


def language_kwargs(user_id: str) -> dict:
    """{"language": ...} for AI calls when the student chose a language other than English, else {}."""
    language = answer_language(user_id)
    return {"language": language} if language != "English" else {}


def update(user_id: str, reminders: bool | None = None, answer_language: str | None = None) -> dict:
    fields = {}
    if reminders is not None:
        fields["pref_reminders"] = int(bool(reminders))
    if answer_language is not None:
        if answer_language not in ANSWER_LANGUAGES:
            raise ValueError(f"Answer language must be one of: {', '.join(ANSWER_LANGUAGES)}.")
        fields["pref_language"] = answer_language
    meta = user_update_metadata(user_id, **fields) if fields else (user_get_by_id(user_id) or {}).get("meta", {})
    prefs = _from_meta(meta)
    with _lock:
        _cache[user_id] = (time.time(), prefs)
    return dict(prefs)


def public(user_id: str) -> dict:
    return {**get(user_id), "languages": list(ANSWER_LANGUAGES), "reminders_available": reminders_available()}


def forget(user_id: str) -> None:
    with _lock:
        _cache.pop(user_id, None)
