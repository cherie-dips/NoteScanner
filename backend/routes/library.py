"""The shared course library: which PDFs are ready for questions, and admin controls for indexing."""
from fastapi import APIRouter, Form

from backend import library, settings
from backend.deps import SignedInUser
from backend.routes.admin import _require_admin

router = APIRouter()


@router.get("/library/status")
def library_status():
    """Public: the course PDFs (storage paths) students can ask about. No account needed."""
    return library.public_status()


@router.get("/admin/library")
def admin_library(user_id: SignedInUser = None):
    """Per-file indexing details and the last sync's summary (admins only)."""
    _require_admin(user_id)
    files = library.indexed_files()
    by_status: dict[str, int] = {}
    for m in files.values():
        by_status[m.get("status") or "unknown"] = by_status.get(m.get("status") or "unknown", 0) + 1
    keep = ("path", "status", "pages", "ocr_pieces", "sarvam_pieces", "chunks", "method", "error", "updated_at")
    return {
        "enabled": settings.LIBRARY_ENABLED,
        "ocr": library._ocr_mode(),
        "sync_hours": settings.LIBRARY_SYNC_HOURS,
        "by_status": by_status,
        "sync": library.sync_state(),
        "files": sorted(({k: m.get(k) for k in keep} for m in files.values()), key=lambda f: f["path"]),
    }


@router.post("/admin/library/sync")
def admin_library_sync(force: bool = Form(False), user_id: SignedInUser = None):
    """Start indexing new or changed course PDFs in the background (admins only)."""
    _require_admin(user_id)
    started = library.sync_in_background(force=force)
    return {
        "started": started,
        "message": "Library sync started." if started else "A library sync is already running.",
        "sync": library.sync_state(),
    }
