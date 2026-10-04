"""
In-memory request limits (per server process).

Three layers use this module:
- an overall cap on every request per network address and per signed-in browser (api.py middleware),
- per-route limits on expensive or sensitive routes (logins, uploads, questions, edits, ...),
- daily caps on paid AI use per user, so one account can't run up the AI bill.

Counters live in memory: they reset when the server restarts and aren't shared between processes.
"""
import math
import os
import threading
import time
from collections import deque

from fastapi import HTTPException, Request

# Scales every limit at once without a code change, e.g. 2 for a busy campus or 0.5 to tighten.
MULTIPLIER = float((os.getenv("RATE_LIMIT_MULTIPLIER") or "1").strip() or "1")

_lock = threading.Lock()
_hits: dict[str, deque] = {}
_windows: dict[str, int] = {}
_MAX_KEYS = 20000

# (bucket, key, max requests, window seconds, quota name or None)
Rule = tuple


def client_ip(request: Request) -> str:
    """Best-effort client address (first X-Forwarded-For entry when behind a proxy)."""
    fwd = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if fwd:
        return fwd
    return request.client.host if request.client else "unknown"


def _human_wait(seconds: int) -> str:
    if seconds < 90:
        return f"{seconds} seconds"
    if seconds < 90 * 60:
        return f"{math.ceil(seconds / 60)} minutes"
    return f"{math.ceil(seconds / 3600)} hours"


def _prune_locked(now: float) -> None:
    """Forget counters with no requests inside their own window (keeps daily counters for a day)."""
    stale = [k for k, q in _hits.items() if not q or q[-1] <= now - _windows.get(k, 0)]
    for k in stale:
        _hits.pop(k, None)
        _windows.pop(k, None)


def check_all(rules: list[Rule]) -> None:
    """
    Check every rule, and record the request against all of them only if none is over its limit,
    so a refused request never uses up another limit. Raises HTTP 429 with a Retry-After header.
    """
    now = time.monotonic()
    with _lock:
        if len(_hits) > _MAX_KEYS:
            _prune_locked(now)
        queues = []
        for bucket, key, limit, window, quota_name in rules:
            limit = max(1, round(limit * MULTIPLIER))
            k = f"{bucket}:{key}"
            q = _hits.setdefault(k, deque())
            _windows[k] = max(_windows.get(k, 0), int(window))
            while q and q[0] <= now - window:
                q.popleft()
            if len(q) >= limit:
                retry = int(q[0] + window - now) + 1
                wait = _human_wait(retry)
                if quota_name:
                    detail = f"You've reached the daily limit of {limit} {quota_name}. You can continue in {wait}."
                else:
                    detail = f"Too many requests. Please try again in {wait}."
                raise HTTPException(status_code=429, detail=detail, headers={"Retry-After": str(retry)})
            queues.append(q)
        for q in queues:
            q.append(now)


def check(bucket: str, key: str, limit: int, window_seconds: int, quota_name: str | None = None) -> None:
    """Record one request for (bucket, key); raise HTTP 429 when over `limit` per window."""
    check_all([(bucket, key, limit, window_seconds, quota_name)])


def per_user(user_id: str, *rules: tuple) -> None:
    """Shorthand for several limits on one user: per_user(uid, ("upload", LIMIT, None), ...)."""
    check_all([(bucket, user_id, limit, window, quota) for bucket, (limit, window), quota in rules])


def reset() -> None:
    """Forget all counters (used by tests)."""
    with _lock:
        _hits.clear()
        _windows.clear()
