"""
Reading text out of uploaded files, and the background queue that processes note uploads.

Reading a PDF or photo with Sarvam Vision can take minutes, so /upload_note only checks the file
and queues it. A worker thread reads the text, saves it for search, and only then adds the file
to the user's folder tree. The browser polls /jobs/{job_id} to show progress.
"""
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from fastapi import HTTPException, UploadFile

from backend import alerts, llm_pipeline, usage
from backend.chroma_store import user_document_upsert, user_lock, vfs_set_tree
from backend.extract_api import extract_text_from_image_bytes
from backend.ingest_api import ingest_text_for_path
from backend.pages import PageStarts, anchor_page_starts, join_pages, pdf_page_texts
from backend.paths import get_vfs_tree_clean
from backend.settings import MAX_ACTIVE_UPLOADS_PER_USER, MAX_UPLOAD_BYTES, MAX_UPLOAD_MB
from backend.vfs_tree import tree_add_file

logger = logging.getLogger(__name__)

IMAGE_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
}
PLAIN_TEXT_EXTS = (".txt", ".md", ".csv", ".json", ".log")
JOB_KEEP_SECONDS = 3600


class ExtractionError(Exception):
    """No text could be read from the file; the message is shown to the user."""


def clean_upload_name(file: UploadFile) -> str:
    raw_name = (file.filename or "upload").replace("\\", "/").split("/")[-1].strip()
    if not raw_name or ".." in raw_name:
        raise HTTPException(status_code=400, detail="Invalid file name.")
    return raw_name


def check_supported_type(raw_name: str) -> None:
    ext = os.path.splitext(raw_name)[1].lower()
    if ext != ".pdf" and ext not in IMAGE_MIME_TYPES and ext not in PLAIN_TEXT_EXTS:
        raise HTTPException(
            status_code=415,
            detail="This file type isn't supported. Upload a PDF, an image, or a text file "
            "(.txt, .md, .csv, .json, .log).",
        )


def read_upload(file: UploadFile) -> bytes:
    """Read the upload, refusing files over MAX_UPLOAD_MB without reading the whole thing."""
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File is too large. The limit is {MAX_UPLOAD_MB:g} MB.",
        )
    return data


@dataclass
class Extracted:
    text: str
    note: str
    page_starts: PageStarts | None = None  # where each PDF page starts in `text`
    pages_sent_to_ai: int = 0              # pages Sarvam Vision was asked to read (for cost tracking)


def _read_pdf(data: bytes) -> Extracted:
    local_error: Exception | None = None
    try:
        local_pages = pdf_page_texts(data)
    except Exception as e:  # not a readable PDF for PyMuPDF; Sarvam may still manage
        local_pages, local_error = [], e
    text, note, ai_pages = llm_pipeline.transcribe_pdf_with_pages(data)
    sent = (len(local_pages) or 1) if llm_pipeline._sarvam_api_key() else 0
    if (text or "").strip():
        # Find each page inside Sarvam's text: its own per-page text first, else PyMuPDF's.
        starts = anchor_page_starts(text, ai_pages) or anchor_page_starts(text, local_pages)
        return Extracted(text, note or "", starts, sent)
    if local_error is not None:
        raise local_error
    joined, starts = join_pages(local_pages)
    return Extracted(joined, note or "used_pdf_parser_fallback", starts if len(starts) > 1 else None, sent)


def extract_text(raw_name: str, data: bytes) -> Extracted:
    """
    Read the text of a PDF, image or plain-text file of a supported type.
    Raises ExtractionError when no text can be read.
    """
    ext = os.path.splitext(raw_name)[1].lower()
    try:
        if ext == ".pdf":
            result = _read_pdf(data)
        elif ext in IMAGE_MIME_TYPES:
            text, note = llm_pipeline.transcribe_handwritten_image(data, IMAGE_MIME_TYPES[ext])
            sent = 1 if llm_pipeline._sarvam_api_key() else 0
            if not (text or "").strip():
                text = extract_text_from_image_bytes(data)
                note = note or "used_tesseract_fallback"
            result = Extracted(text, note or "", None, sent)
        else:
            result = Extracted(data.decode("utf-8", errors="replace"), "plain_text")
    except Exception as e:
        logger.exception("Text extraction failed for %s", raw_name)
        raise ExtractionError("Couldn't read this file. It may be damaged or password-protected.") from e
    stripped = (result.text or "").strip()
    if not stripped:
        raise ExtractionError("Couldn't find any text in this file.")
    if stripped != result.text and result.page_starts:
        shift = len(result.text) - len(result.text.lstrip())
        result.page_starts = [(max(0, off - shift), page) for off, page in result.page_starts]
    result.text = stripped
    return result


def extract_text_or_http_error(raw_name: str, data: bytes) -> Extracted:
    try:
        return extract_text(raw_name, data)
    except ExtractionError as e:
        raise HTTPException(status_code=422, detail=str(e))


def save_note_text(user_id: str, folder: str, raw_name: str, text: str, page_starts: PageStarts | None = None) -> dict:
    """Save text for search and as the file's stored text, then add the file to the tree."""
    rel_path = f"{folder}/{raw_name}" if folder else raw_name
    ingest_result = ingest_text_for_path(user_id, rel_path, raw_name, text, page_starts)
    try:
        user_document_upsert(user_id, rel_path, text, raw_name)
    except Exception:
        # Search chunks are the primary copy; the full-text copy is used for viewing/editing.
        logger.warning("Stored-text write failed for %s", rel_path, exc_info=True)
    with user_lock(user_id):
        tree = get_vfs_tree_clean(user_id)
        vfs_set_tree(user_id, tree_add_file(tree, folder, raw_name))
    return ingest_result


# ---------- Background upload jobs ----------
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="upload")
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _public(job: dict) -> dict:
    return {k: job[k] for k in ("job_id", "status", "path", "name", "error", "message")}


def _prune_locked(now: float) -> None:
    old = [
        jid
        for jid, j in _jobs.items()
        if j["status"] in ("done", "failed") and now - j["updated_at"] > JOB_KEEP_SECONDS
    ]
    for jid in old:
        del _jobs[jid]


def _set(job_id: str, **fields) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job:
            job.update(fields, updated_at=time.time())


def _process(job_id: str, user_id: str, folder: str, raw_name: str, data: bytes) -> None:
    _set(job_id, status="processing")
    try:
        extracted = extract_text(raw_name, data)
        usage.record(user_id, pages_read=extracted.pages_sent_to_ai)
        result = save_note_text(user_id, folder, raw_name, extracted.text, extracted.page_starts)
        _set(job_id, status="done", message=f"Ready: {result.get('chunks_created', 0)} searchable sections.")
    except ExtractionError as e:
        usage.record(user_id, upload_failures=1)
        _set(job_id, status="failed", error=str(e))
    except Exception as e:
        logger.exception("Upload job %s failed", job_id)
        usage.record(user_id, upload_failures=1)
        alerts.notify("upload-crash", f"Upload job failed unexpectedly: {type(e).__name__}: {e}")
        _set(job_id, status="failed", error=f"Couldn't save '{raw_name}' for search. Please try again.")


def submit_upload_job(user_id: str, folder: str, raw_name: str, data: bytes) -> dict:
    now = time.time()
    with _jobs_lock:
        _prune_locked(now)
        active = sum(1 for j in _jobs.values() if j["user_id"] == user_id and j["status"] in ("queued", "processing"))
        if active >= MAX_ACTIVE_UPLOADS_PER_USER:
            raise HTTPException(
                status_code=429,
                detail=f"You already have {active} files being processed. Please wait for them to finish.",
            )
        job_id = uuid.uuid4().hex
        job = {
            "job_id": job_id,
            "user_id": user_id,
            "status": "queued",
            "path": f"{folder}/{raw_name}" if folder else raw_name,
            "name": raw_name,
            "error": None,
            "message": None,
            "created_at": now,
            "updated_at": now,
        }
        _jobs[job_id] = job
        # Snapshot before the worker starts: a fast failure must not change the reply mid-build.
        reply = _public(job)
    _executor.submit(_process, job_id, user_id, folder, raw_name, data)
    return reply


def get_job(user_id: str, job_id: str) -> dict | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        return _public(job) if job and job["user_id"] == user_id else None


def list_jobs(user_id: str) -> list[dict]:
    with _jobs_lock:
        _prune_locked(time.time())
        mine = [j for j in _jobs.values() if j["user_id"] == user_id]
    mine.sort(key=lambda j: j["created_at"], reverse=True)
    return [_public(j) for j in mine]


def forget_user_jobs(user_id: str) -> None:
    with _jobs_lock:
        for jid in [jid for jid, j in _jobs.items() if j["user_id"] == user_id]:
            del _jobs[jid]
