"""Student feedback, and the admin page (usage, cost and feedback for running a pilot)."""
import datetime as dt
import time

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import JSONResponse

from backend import feedback, rate_limit, settings, usage
from backend.chroma_store import user_get_by_id
from backend.deps import SignedInUser
from backend.library import USAGE_ID as LIBRARY_USAGE_ID

router = APIRouter()

LIMIT_FEEDBACK_PER_USER = (30, 600)


def is_admin(user_id: str) -> bool:
    if not settings.ADMIN_EMAILS:
        return False
    user = user_get_by_id(user_id)
    return bool(user and (user.get("email") or "").lower() in settings.ADMIN_EMAILS)


def _require_admin(user_id: str) -> None:
    if not is_admin(user_id):
        raise HTTPException(status_code=403, detail="Only admins can see this page.")


@router.post("/feedback")
def send_feedback(
    kind: str = Form("general"),
    rating: str = Form(""),
    comment: str = Form(""),
    question: str = Form(""),
    answer: str = Form(""),
    sources: str = Form(""),
    selection_stage: str = Form(""),
    user_id: SignedInUser = None,
):
    if kind not in feedback.KINDS or rating not in feedback.RATINGS:
        return JSONResponse({"error": "kind must be answer/general and rating up/down/empty."}, status_code=400)
    if kind == "general" and not comment.strip():
        return JSONResponse({"error": "Please write some feedback first."}, status_code=400)
    rate_limit.check("feedback", user_id, *LIMIT_FEEDBACK_PER_USER)
    feedback.add(user_id, kind, rating, selection_stage, comment=comment, question=question, answer=answer, sources=sources)
    if rating:
        usage.record(user_id, **{f"ratings_{rating}": 1})
    return JSONResponse({"message": "Thanks for the feedback!"}, status_code=201)


@router.get("/admin/stats")
def admin_stats(days: int = 7, user_id: SignedInUser = None):
    _require_admin(user_id)
    days = max(1, min(int(days), 90))
    today = usage.today()
    start = today - dt.timedelta(days=days - 1)
    records = usage.records_since(start)

    totals = {k: 0 for k in usage.COUNTERS}
    daily: dict[str, dict] = {}
    for i in range(days):
        d = (start + dt.timedelta(days=i)).isoformat()
        daily[d] = {"day": d, "active_users": 0, "questions": 0, "uploads": 0, "reviews": 0, "study_requests": 0}
    active: set[str] = set()
    for r in records:
        for k in usage.COUNTERS:
            totals[k] += int(r.get(k) or 0)
        if r.get("user_id") == LIBRARY_USAGE_ID:
            continue  # pages the shared library sent to OCR: counted in the totals, not a student
        row = daily.get(r.get("day"))
        if row is not None:
            row["active_users"] += 1
            for k in ("questions", "uploads", "reviews", "study_requests"):
                row[k] += int(r.get(k) or 0)
        active.add(r.get("user_id"))

    rated = totals["ratings_up"] + totals["ratings_down"]
    prices = usage.prices_set()
    return {
        "days": days,
        "totals": {
            "active_users": len(active),
            **{k: totals[k] for k in ("questions", "uploads", "pages_read", "upload_failures", "study_requests",
                                       "reviews", "ratings_up", "ratings_down", "prompt_tokens", "completion_tokens")},
            "helpful_share": round(totals["ratings_up"] / rated, 3) if rated else None,
            "estimated_cost": round(usage.cost_of(totals), 2) if prices else None,
        },
        "daily": list(daily.values()),
        "budget": {
            "monthly_budget": settings.AI_MONTHLY_BUDGET or None,
            "month_cost": round(usage.month_cost(), 2) if prices else None,
            "currency": settings.AI_PRICE_CURRENCY,
        },
    }


@router.get("/admin/feedback")
def admin_feedback(limit: int = 50, user_id: SignedInUser = None):
    _require_admin(user_id)
    rows = feedback.recent(limit=max(1, min(int(limit), 200)), since=int(time.time()) - 90 * 86400)
    emails: dict[str, str] = {}
    for r in rows:
        uid = r.pop("user_id", "") or ""
        if uid not in emails:
            user = user_get_by_id(uid) if uid else None
            emails[uid] = (user or {}).get("email") or "(deleted account)"
        r["user_email"] = emails[uid]
    return {"feedback": rows}
