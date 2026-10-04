"""
Counting what each student does per day: questions, uploads, pages read by Sarvam Vision, AI tokens,
flashcard reviews, ratings. Used for the admin page, cost estimates, streaks, and the monthly budget.

One small record per user per day in the `usage_daily` collection.
"""
import datetime as dt
import logging
import threading
import time
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from backend import settings
from backend.chroma_store import _dummy_embedding, _get_or_create_collection, get_all, short_id

logger = logging.getLogger(__name__)

COUNTERS = (
    "questions",
    "uploads",
    "pages_read",
    "upload_failures",
    "study_requests",
    "reviews",
    "prompt_tokens",
    "completion_tokens",
    "ratings_up",
    "ratings_down",
)

_lock = threading.Lock()
_month_cost: dict[str, float] = {}  # "YYYY-MM" -> estimated cost so far (filled lazily from the database)


def tz() -> ZoneInfo:
    try:
        return ZoneInfo(settings.APP_TIMEZONE)
    except Exception:
        return ZoneInfo("UTC")


def today(now: float | None = None) -> dt.date:
    return dt.datetime.fromtimestamp(now if now is not None else time.time(), tz()).date()


def day_num(day: dt.date) -> int:
    return day.year * 10000 + day.month * 100 + day.day


def _collection():
    return _get_or_create_collection("usage_daily")


def prices_set() -> bool:
    return any(
        (settings.AI_PRICE_PER_PAGE, settings.AI_PRICE_PER_1K_INPUT_TOKENS, settings.AI_PRICE_PER_1K_OUTPUT_TOKENS)
    )


def cost_of(counts: dict) -> float:
    return (
        float(counts.get("pages_read") or 0) * settings.AI_PRICE_PER_PAGE
        + float(counts.get("prompt_tokens") or 0) / 1000 * settings.AI_PRICE_PER_1K_INPUT_TOKENS
        + float(counts.get("completion_tokens") or 0) / 1000 * settings.AI_PRICE_PER_1K_OUTPUT_TOKENS
    )


def record(user_id: str, **increments: int) -> None:
    """Add to today's counters for a user. Never raises: counting must not break the request."""
    increments = {k: int(v) for k, v in increments.items() if k in COUNTERS and v}
    if not user_id or not increments:
        return
    day = today()
    month = f"{day.year:04d}-{day.month:02d}"
    rid = short_id("u", user_id, day.isoformat())
    try:
        with _lock:
            col = _collection()
            res = col.get(ids=[rid], include=["metadatas"])
            meta = dict((res.get("metadatas") or [{}])[0] or {}) if res.get("ids") else {
                "user_id": user_id,
                "day": day.isoformat(),
                "day_num": day_num(day),
                "month": month,
            }
            for k, v in increments.items():
                meta[k] = int(meta.get(k) or 0) + v
            col.upsert(ids=[rid], documents=[""], embeddings=[_dummy_embedding()], metadatas=[meta])
            if month in _month_cost:
                _month_cost[month] += cost_of(increments)
    except Exception:
        logger.warning("Usage counting failed for %s", user_id, exc_info=True)


def records_since(start: dt.date, user_id: str | None = None) -> list[dict]:
    where: dict = {"day_num": {"$gte": day_num(start)}}
    if user_id:
        where = {"$and": [where, {"user_id": user_id}]}
    res = get_all(_collection(), where=where, include=["metadatas"])
    return [m or {} for m in res.get("metadatas") or []]


def month_cost(now: float | None = None) -> float:
    day = today(now)
    month = f"{day.year:04d}-{day.month:02d}"
    with _lock:
        if month not in _month_cost:
            res = get_all(_collection(), where={"month": month}, include=["metadatas"])
            _month_cost[month] = sum(cost_of(m or {}) for m in res.get("metadatas") or [])
        return _month_cost[month]


def check_budget() -> None:
    """Pause paid AI work (HTTP 503) once this month's estimated cost reaches AI_MONTHLY_BUDGET."""
    if settings.AI_MONTHLY_BUDGET <= 0 or not prices_set():
        return
    try:
        spent = month_cost()
    except Exception:
        logger.warning("Budget check failed; allowing the request", exc_info=True)
        return
    if spent >= settings.AI_MONTHLY_BUDGET:
        raise HTTPException(
            status_code=503,
            detail="NoteScanner has used this month's AI budget, so reading files, answers and study "
            "tools are paused until next month. Your notes and flashcard reviews still work.",
        )


def reset_cache() -> None:
    with _lock:
        _month_cost.clear()


def delete_user(user_id: str) -> None:
    from backend.chroma_store import delete_ids

    col = _collection()
    delete_ids(col, get_all(col, where={"user_id": user_id}, include=[])["ids"])
