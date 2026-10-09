"""Chat: temporary chat uploads (+ button) and answering questions from notes."""
import json
import logging
from datetime import datetime

import numpy as np
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from backend import alerts, chat_cache, library, llm_pipeline, preferences, rate_limit, uploads, usage
from backend.deps import SignedInUser
from backend.ingest_api import embed_texts
from backend.paths import file_paths, get_vfs_tree_clean, strip_storage_path
from backend.search import SearchResult, course_of, find_context
from backend.settings import (
    LIMIT_QUESTIONS_PER_DAY,
    LIMIT_QUESTIONS_PER_USER,
    LIMIT_UPLOADS_PER_DAY,
    LIMIT_UPLOADS_PER_USER,
    MAX_CHAT_FILES_PER_SESSION,
    MAX_CHAT_UPLOAD_CHARS,
)

logger = logging.getLogger(__name__)
router = APIRouter()

AI_UNAVAILABLE = "The AI couldn't answer right now. Please try again."


def _split_text(t: str, size: int = 900, overlap: int = 140) -> list[str]:
    tx = (t or "").strip()
    if not tx:
        return []
    out: list[str] = []
    i = 0
    n = len(tx)
    while i < n:
        out.append(tx[i : i + size])
        i += max(1, size - overlap)
    return out


@router.post("/chat/upload_ephemeral")
def chat_upload_ephemeral(chat_session_id: str = Form(...), file: UploadFile = File(...), user_id: SignedInUser = None):
    rate_limit.per_user(
        user_id,
        ("upload", LIMIT_UPLOADS_PER_USER, None),
        ("upload-day", LIMIT_UPLOADS_PER_DAY, "uploads"),
    )
    sid = (chat_session_id or "").strip()
    if not sid:
        return JSONResponse({"error": "chat_session_id is required."}, status_code=400)
    if chat_cache.count(user_id, sid) >= MAX_CHAT_FILES_PER_SESSION:
        return JSONResponse(
            {"error": f"This chat already has {MAX_CHAT_FILES_PER_SESSION} files. Start a new chat to add more."},
            status_code=400,
        )
    raw_name = uploads.clean_upload_name(file)
    uploads.check_supported_type(raw_name)
    usage.check_budget()
    data = uploads.read_upload(file)
    extracted = uploads.extract_text_or_http_error(raw_name, data)
    usage.record(user_id, uploads=1, pages_read=extracted.pages_sent_to_ai)
    text, source = extracted.text, extracted.note

    truncated = len(text) > MAX_CHAT_UPLOAD_CHARS
    text = text[:MAX_CHAT_UPLOAD_CHARS]
    # Embed every chunk once now, so questions search the whole file instead of only its start.
    chunks = _split_text(text)
    embeddings = np.asarray(embed_texts(chunks), dtype=np.float32)
    count = chat_cache.add(
        user_id,
        sid,
        {
            "name": raw_name,
            "chunks": chunks,
            "embeddings": embeddings,
            "source": source,
            "uploaded_at": datetime.now().isoformat(),
        },
    )
    return JSONResponse(
        {
            "message": f"Cached '{raw_name}' for this chat session.",
            "chat_session_id": sid,
            "cached_files_count": count,
            "text_chars": len(text),
            "text_truncated": truncated,
            "extractor_note": source,
        }
    )


@router.post("/chat/session/clear")
def clear_chat_session_cache(chat_session_id: str = Form(...), user_id: SignedInUser = None):
    sid = (chat_session_id or "").strip()
    if not sid:
        return JSONResponse({"error": "chat_session_id is required."}, status_code=400)
    deleted = chat_cache.clear(user_id, sid)
    return JSONResponse({"message": "Chat cache cleared.", "deleted_files": deleted})


def _ai_failed(err: str) -> None:
    logger.warning("Answer generation failed: %s", err)
    alerts.notify("ai-unavailable", f"Sarvam chat failed: {err}")


def _prepare_question(
    user_id: str,
    query: str,
    highlight: str,
    opened_file_path: str,
    course_path: str,
    include_course_context: bool,
    chat_session_id: str,
    library_path: str = "",
) -> tuple[str, str, str, list[dict], SearchResult]:
    """Validate the question and find the note excerpts to answer it from (HTTP errors raise)."""
    q = (query or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="Query is empty.")
    rate_limit.per_user(
        user_id,
        ("query", LIMIT_QUESTIONS_PER_USER, None),
        ("query-day", LIMIT_QUESTIONS_PER_DAY, "questions"),
    )
    usage.check_budget()
    all_paths = file_paths(get_vfs_tree_clean(user_id))
    opened = strip_storage_path(opened_file_path or "")
    course = course_of(opened, strip_storage_path(course_path or ""))
    ephemeral = chat_cache.items(user_id, chat_session_id)
    hl = (highlight or "").strip()
    full_q = f'Regarding this selection:\n"""{hl}"""\n\n{q}' if hl else q
    result = find_context(
        user_id, full_q, all_paths, opened, course, ephemeral, include_course_context, library_path=library_path
    )
    if not result.selected:
        raise HTTPException(
            status_code=400,
            detail=library.NOT_READY
            if library.scope_of(library_path)[1]
            else "No notes found to answer from. Upload notes, open a file, or attach one with +.",
        )
    usage.record(user_id, questions=1)
    return q, full_q, opened, ephemeral, result


def _answer_metadata(q: str, opened: str, ephemeral: list[dict], result: SearchResult) -> dict:
    return {
        "query": q,
        "source_documents": result.source_documents(),
        "grounded_on_path": opened,
        "ephemeral_files_used": len(ephemeral),
        "selection_stage": result.stage,
        "score_ephemeral_best": result.best_scores.get("ephemeral", -1.0),
        "score_library_file_best": result.best_scores.get("library_file", -1.0),
        "score_library_subject_best": result.best_scores.get("library_subject", -1.0),
        "score_opened_best": result.best_scores.get("opened", -1.0),
        "score_course_best": result.best_scores.get("course", -1.0),
        "score_all_notes_best": result.best_scores.get("all_notes", -1.0),
    }


@router.post("/query_folder")
def query_notes(
    query: str = Form(...),
    highlight: str = Form(""),
    opened_file_path: str = Form(""),
    course_path: str = Form(""),
    include_course_context: bool = Form(True),
    chat_session_id: str = Form(""),
    library_path: str = Form(""),
    user_id: SignedInUser = None,
):
    q, full_q, opened, ephemeral, result = _prepare_question(
        user_id, query, highlight, opened_file_path, course_path, include_course_context, chat_session_id, library_path
    )
    llm_pipeline.take_usage()
    answer, err = llm_pipeline.sarvam_rag_answer(full_q, result.context(), **preferences.language_kwargs(user_id))
    usage.record(user_id, **llm_pipeline.take_usage())
    if err:
        _ai_failed(err)
        return JSONResponse({"error": AI_UNAVAILABLE, "query": q, "answer": "", "source_documents": []}, status_code=503)
    return JSONResponse({**_answer_metadata(q, opened, ephemeral, result), "answer": answer or ""})


@router.post("/query_folder/stream")
def query_notes_stream(
    query: str = Form(...),
    highlight: str = Form(""),
    opened_file_path: str = Form(""),
    course_path: str = Form(""),
    include_course_context: bool = Form(True),
    chat_session_id: str = Form(""),
    library_path: str = Form(""),
    user_id: SignedInUser = None,
):
    """
    Same as /query_folder, but the answer arrives as it's written. Newline-delimited JSON:
    {"type":"meta",...sources} then {"type":"delta","text":...}* then {"type":"done"} or {"type":"error"}.
    `library_path` asks about a shared course PDF (see library.py) instead of an uploaded file.
    """
    q, full_q, opened, ephemeral, result = _prepare_question(
        user_id, query, highlight, opened_file_path, course_path, include_course_context, chat_session_id, library_path
    )
    context = result.context()
    lang = preferences.language_kwargs(user_id)

    def lines():
        yield json.dumps({"type": "meta", **_answer_metadata(q, opened, ephemeral, result)}) + "\n"
        written: list[str] = []
        try:
            for piece in llm_pipeline.sarvam_rag_answer_stream(full_q, context, **lang):
                written.append(piece)
                yield json.dumps({"type": "delta", "text": piece}) + "\n"
        except Exception as e:
            _ai_failed(str(e))
            yield json.dumps({"type": "error", "error": AI_UNAVAILABLE}) + "\n"
            return
        finally:
            # Streaming replies don't report tokens; estimate them for the cost figures.
            usage.record(
                user_id,
                prompt_tokens=llm_pipeline.estimate_tokens(full_q + context),
                completion_tokens=llm_pipeline.estimate_tokens("".join(written)) if written else 0,
            )
        if not written:
            _ai_failed("empty streamed answer")
            yield json.dumps({"type": "error", "error": AI_UNAVAILABLE}) + "\n"
            return
        yield json.dumps({"type": "done"}) + "\n"

    return StreamingResponse(lines(), media_type="application/x-ndjson", headers={"Cache-Control": "no-cache"})
