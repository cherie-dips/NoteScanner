from fastapi import FastAPI, UploadFile, File, Form, Depends, Request, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import os
import re
import uuid
import time
from dotenv import load_dotenv
import copy
import json
from datetime import datetime
from typing import Annotated
from sentence_transformers import SentenceTransformer

from backend.chroma_store import (
    user_exists_by_email,
    user_create,
    user_get_by_email,
    user_get_by_id,
    user_document_upsert,
    user_document_delete_many,
    user_document_delete_by_path,
    get_user_collection,
    delete_user_notes_collection,
    delete_user_notes_by_paths,
    vfs_get_tree,
    vfs_set_tree,
    delete_user_vfs_collection,
    user_document_rename_path,
    user_documents_relocate_prefix,
    onenote_oauth_state_create,
    onenote_oauth_state_pop_user_id,
    onenote_token_get,
    onenote_token_upsert,
    user_document_get_content,
    user_notes_concat_text_for_path,
)

from backend.file_meta import (
    load_file_meta,
    set_entry,
    delete_key,
    delete_keys_for_prefix,
    chunk_paths_for_file,
    file_meta_rename_path,
    file_meta_relocate_prefix,
)
from backend.ingest_api import (
    ingest_text_for_path,
    refresh_metadata_for_paths,
    chunks_relocate_path,
    chunks_relocate_folder_prefix,
)
from backend.vfs_tree import (
    tree_add_folder,
    tree_add_file,
    tree_remove_path,
    tree_move_path,
    tree_has_folder,
    flatten_file_paths,
)
from backend import llm_pipeline
from backend import onenote_sync

from backend.auth import (
    hash_password,
    verify_password,
    create_session,
    get_user_id_from_session,
    get_effective_user,
    SESSION_HEADER,
    GUEST_HEADER,
)

load_dotenv()

app = FastAPI()
_query_embed_model = None
_chat_upload_cache: dict[str, list[dict]] = {}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _get_query_embedder():
    global _query_embed_model
    if _query_embed_model is None:
        _query_embed_model = SentenceTransformer("all-MiniLM-L6-v2")
    return _query_embed_model


def _chat_cache_key(user_id: str, chat_session_id: str) -> str:
    return f"{user_id}::{(chat_session_id or '').strip()}"


def _chat_cache_add(user_id: str, chat_session_id: str, item: dict) -> None:
    key = _chat_cache_key(user_id, chat_session_id)
    if not key.endswith("::"):
        _chat_upload_cache.setdefault(key, []).append(item)


def _chat_cache_items(user_id: str, chat_session_id: str) -> list[dict]:
    key = _chat_cache_key(user_id, chat_session_id)
    return list(_chat_upload_cache.get(key, []))


def _chat_cache_clear(user_id: str, chat_session_id: str) -> int:
    key = _chat_cache_key(user_id, chat_session_id)
    old = _chat_upload_cache.pop(key, [])
    return len(old)

# ---------- Auth: register & login (simple session, no JWT) ----------
@app.post("/register")
def register(email: str = Form(...), password: str = Form(...), name: str = Form("")):
    try:
        if user_exists_by_email(email):
            return JSONResponse({"error": "Email already registered."}, status_code=400)
        user_id = user_create(
            email.lower(),
            hash_password(password),
            name or email.split("@")[0],
        )
        session_id = create_session(user_id)
        u = user_get_by_id(user_id)
        display_name = (u and u.get("name")) or email.split("@")[0]
        return JSONResponse({"session_id": session_id, "user_id": user_id, "name": display_name})
    except Exception:
        return JSONResponse(
            {"error": "Database unavailable. Please try again later."},
            status_code=503,
        )


@app.post("/login")
def login(email: str = Form(...), password: str = Form(...)):
    try:
        user = user_get_by_email(email)
        if not user or not verify_password(password, user["hashed_password"]):
            return JSONResponse({"error": "Invalid email or password."}, status_code=401)
        user_id = user["user_id"]
        session_id = create_session(user_id)
        name = user.get("name") or (user.get("email") or "").split("@")[0]
        return JSONResponse({"session_id": session_id, "user_id": user_id, "name": name})
    except Exception:
        return JSONResponse(
            {"error": "Database unavailable. Please try again later."},
            status_code=503,
        )


@app.get("/me")
def get_me(request: Request):
    """Return current user info (name) for the session. Requires valid X-Session-Id."""
    session_id = request.headers.get(SESSION_HEADER)
    user_id = get_user_id_from_session(session_id)
    if not user_id:
        return JSONResponse({"error": "Not signed in."}, status_code=401)
    user = user_get_by_id(user_id)
    if not user:
        return JSONResponse({"error": "User not found."}, status_code=404)
    name = user.get("name") or (user.get("email") or "").split("@")[0]
    return JSONResponse({"name": name or "User"})


@app.get("/guest_id")
def get_guest_id():
    """Return a new guest id for anonymous use. Frontend stores it and sends X-Guest-Id header."""
    return JSONResponse({"guest_id": str(uuid.uuid4())})


# ---------- Resolve user/guest from request ----------
def get_effective_user_dep(request: Request) -> tuple[str, bool]:
    try:
        return get_effective_user(request)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _require_signed_in(effective: tuple[str, bool]) -> str:
    user_id, is_guest = effective
    if is_guest:
        raise HTTPException(status_code=401, detail="Sign in required for OneNote integration.")
    return user_id


# ---------- Delete file or folder ----------
def _normalize_relative_path(path: str) -> str:
    """Use forward slashes and strip leading slashes for cross-platform consistency."""
    if not path or not path.strip():
        return ""
    return path.replace("\\", "/").strip("/")


def _is_likely_user_id_segment(seg: str) -> bool:
    if not seg:
        return False
    if seg.startswith("guest_"):
        return True
    if len(seg) == 36 and seg.count("-") == 4:
        return True
    return False


def _strip_storage_path(p: str) -> str:
    """
    Remove legacy server-side prefixes from virtual paths so course folders stay at the root
    (e.g. user_notes/<id>/Course/a.pdf -> Course/a.pdf). Never persist user_notes in vfs.
    """
    p = _normalize_relative_path(p)
    if not p:
        return ""
    parts = [x for x in p.split("/") if x and x != "."]
    while parts and parts[0].lower() == "user_notes":
        parts.pop(0)
    if parts and _is_likely_user_id_segment(parts[0]):
        parts.pop(0)
    return "/".join(parts)


def _normalize_vfs_tree(tree: list) -> list:
    """Deep copy of explorer tree with every path passed through _strip_storage_path."""
    out = copy.deepcopy(tree or [])

    def rec(nodes: list) -> None:
        for n in nodes:
            if "path" in n:
                n["path"] = _strip_storage_path(str(n.get("path") or ""))
            if n.get("type") == "folder":
                rec(n.get("children") or [])

    rec(out)
    return out


def _get_vfs_tree_clean(user_id: str) -> list:
    """Return vfs tree with no user_notes/legacy id prefixes; persist if corrected."""
    raw = vfs_get_tree(user_id)
    fixed = _normalize_vfs_tree(raw)
    if fixed != raw:
        vfs_set_tree(user_id, fixed)
    return fixed


def _vfs_entry_kind(tree: list, target_path: str) -> str | None:
    """Return 'file', 'folder', or None if path is not in the virtual tree."""
    tp = _strip_storage_path(target_path)
    if not tp:
        return None
    if tp in {p.replace("\\", "/") for p in flatten_file_paths(tree)}:
        return "file"

    def folder_at(nodes: list) -> bool:
        for n in nodes:
            p = _normalize_relative_path(n.get("path") or "")
            if n.get("type") == "folder":
                if p == tp:
                    return True
                if folder_at(n.get("children") or []):
                    return True
        return False

    return "folder" if folder_at(tree) else None


def _path_exists_in_vfs(tree: list, rel: str) -> bool:
    rel = _strip_storage_path(rel)
    if rel in {p.replace("\\", "/") for p in flatten_file_paths(tree)}:
        return True

    def folder_at(nodes: list) -> bool:
        for n in nodes:
            p = _normalize_relative_path(n.get("path") or "")
            if n.get("type") == "folder":
                if p == rel:
                    return True
                if folder_at(n.get("children") or []):
                    return True
        return False

    return folder_at(tree)


@app.post("/delete_path")
def delete_path(
    path: str = Form(...),
    kind: str = Form(...),  # "file" | "folder"
    effective: Annotated[tuple[str, bool], Depends(get_effective_user_dep)] = None,
):
    """Remove a file or folder from the virtual tree and Chroma (no server-side binaries)."""
    user_id, _ = effective
    path = _strip_storage_path(path)
    if ".." in path:
        return JSONResponse({"error": "Invalid path."}, status_code=400)
    tree = _get_vfs_tree_clean(user_id)
    if kind == "file":
        if _vfs_entry_kind(tree, path) != "file":
            return JSONResponse({"error": "Not a file or not found."}, status_code=400)
        stem, extp = os.path.splitext(path)
        vfs_set_tree(user_id, tree_remove_path(tree, path))
        user_document_delete_by_path(user_id, path)
        if extp:
            user_document_delete_by_path(user_id, stem + ".txt")
        try:
            col = get_user_collection(user_id)
            ids_to_drop = []
            for p in (path, path.replace(os.path.splitext(path)[1], ".txt") if extp else path):
                res = col.get(where={"path": p}, include=[])
                ids_to_drop.extend(res.get("ids") or [])
            if ids_to_drop:
                col.delete(ids=ids_to_drop)
        except Exception:
            pass
        delete_key(user_id, path)
        if extp:
            delete_key(user_id, stem + ".txt")
        return JSONResponse({"message": "File deleted."})
    if kind == "folder":
        if _vfs_entry_kind(tree, path) != "folder":
            return JSONResponse({"error": "Not a folder or not found."}, status_code=400)
        vfs_set_tree(user_id, tree_remove_path(tree, path))
        safe_prefix = path.rstrip("/").replace("\\", "/")

        def path_prefix_match(p):
            np = p.replace("\\", "/")
            return np == safe_prefix or np.startswith(safe_prefix + "/")

        user_document_delete_many(user_id, path_filter=path_prefix_match)
        try:
            col = get_user_collection(user_id)
            res = col.get(include=["metadatas"])
            ids_to_drop = []
            for i, meta in enumerate(res.get("metadatas") or []):
                p = (meta or {}).get("path") or (meta or {}).get("source_path") or ""
                p = p.replace("\\", "/")
                if p == safe_prefix or p.startswith(safe_prefix + "/"):
                    ids_to_drop.append(res["ids"][i])
            if ids_to_drop:
                col.delete(ids=ids_to_drop)
        except Exception:
            pass
        delete_keys_for_prefix(user_id, path)
        return JSONResponse({"message": "Folder deleted."})
    return JSONResponse({"error": "Invalid kind. Use 'file' or 'folder'."}, status_code=400)


@app.post("/move_path")
def move_path(
    from_path: str = Form(...),
    to_folder: str = Form(""),
    effective: Annotated[tuple[str, bool], Depends(get_effective_user_dep)] = None,
):
    """Move a file or folder in the virtual tree and update Chroma paths."""
    user_id, _ = effective
    from_path = _strip_storage_path(from_path)
    to_folder = _strip_storage_path(to_folder)
    if ".." in from_path or ".." in to_folder:
        return JSONResponse({"error": "Invalid path."}, status_code=400)
    tree = _get_vfs_tree_clean(user_id)
    kind = _vfs_entry_kind(tree, from_path)
    if not kind:
        return JSONResponse({"error": "Path not found."}, status_code=400)
    name = os.path.basename(from_path.replace("\\", "/"))
    dest_rel = (os.path.join(to_folder, name).replace("\\", "/") if to_folder else name).replace("\\", "/")
    if dest_rel == from_path.replace("\\", "/"):
        return JSONResponse({"message": "Already there.", "path": dest_rel})

    if _path_exists_in_vfs(tree, dest_rel):
        return JSONResponse({"error": f"'{name}' already exists in destination."}, status_code=400)

    new_tree = tree_move_path(tree, from_path, to_folder)
    vfs_set_tree(user_id, new_tree)

    full_dest = dest_rel
    was_file = kind == "file"
    if was_file:
        stem, extp = os.path.splitext(from_path)
        chunks_relocate_path(user_id, from_path, full_dest)
        if extp:
            chunks_relocate_path(
                user_id,
                stem + ".txt",
                os.path.splitext(full_dest)[0] + ".txt",
            )
        user_document_rename_path(user_id, from_path, full_dest)
        if extp:
            user_document_rename_path(
                user_id,
                stem + ".txt",
                os.path.splitext(full_dest)[0] + ".txt",
            )
        file_meta_rename_path(user_id, from_path, full_dest)
        if extp:
            file_meta_rename_path(user_id, stem + ".txt", os.path.splitext(full_dest)[0] + ".txt")
    else:
        old_prefix = from_path.rstrip("/").replace("\\", "/")
        new_prefix = full_dest.rstrip("/").replace("\\", "/")
        chunks_relocate_folder_prefix(user_id, old_prefix, new_prefix)
        user_documents_relocate_prefix(user_id, old_prefix, new_prefix)
        file_meta_relocate_prefix(user_id, old_prefix, new_prefix)

    return JSONResponse({"message": "Moved.", "path": dest_rel})


# ---------- Note operations (VFS + Chroma for signed-in and guest; guest user_id is guest_<uuid>) ----------
@app.post("/create_folder")
def create_folder(
    path: str = Form(""),
    name: str = Form(...),
    effective: Annotated[tuple[str, bool], Depends(get_effective_user_dep)] = None,
):
    user_id, _ = effective
    path = _strip_storage_path(path)
    name = (name or "").strip().replace("\\", "/").strip("/")
    if not name or ".." in name or "/" in name:
        return JSONResponse({"error": "Invalid folder name."}, status_code=400)
    tree = _get_vfs_tree_clean(user_id)
    if path and not tree_has_folder(tree, path):
        return JSONResponse({"error": f"Folder '{path}' does not exist."}, status_code=400)
    vfs_set_tree(user_id, tree_add_folder(tree, path, name))
    rel = f"{path}/{name}".replace("//", "/").strip("/") if path else name
    return JSONResponse({"message": f"Folder '{name}' created.", "path": rel})


@app.post("/create_file")
def create_file(
    path: str = Form(""),
    name: str = Form(...),
    effective: Annotated[tuple[str, bool], Depends(get_effective_user_dep)] = None,
):
    user_id, _ = effective
    path = _strip_storage_path(path)
    if path and ".." in path:
        return JSONResponse({"error": "Invalid path."}, status_code=400)
    tree = _get_vfs_tree_clean(user_id)
    if path and not tree_has_folder(tree, path):
        return JSONResponse({"error": f"Folder '{path}' does not exist."}, status_code=400)
    filename = name if "." in name else f"{name}.txt"
    if "/" in filename.replace("\\", "/") or ".." in filename:
        return JSONResponse({"error": "Invalid file name."}, status_code=400)
    vfs_set_tree(user_id, tree_add_file(tree, path, filename))
    rel_path = f"{path}/{filename}".replace("//", "/").strip("/") if path else filename
    user_document_upsert(user_id, rel_path, "", filename)
    return JSONResponse({"message": f"File '{filename}' created.", "path": rel_path})


def _safe_user_document_upsert(user_id: str, path: str, content: str, original_filename: str) -> str | None:
    """Best-effort cache write; should not block ingestion/query pipeline."""
    try:
        user_document_upsert(user_id, path, content, original_filename)
        return None
    except Exception as e:
        return f"user_documents cache write skipped: {str(e)}"


@app.post("/upload_note")
def upload_note(
    path: str = Form(""),
    file: UploadFile = File(...),
    effective: Annotated[tuple[str, bool], Depends(get_effective_user_dep)] = None,
):
    user_id, _ = effective
    path = _strip_storage_path(path)
    if path and ".." in path:
        return JSONResponse({"error": "Invalid path."}, status_code=400)
    tree = _get_vfs_tree_clean(user_id)
    if path and not tree_has_folder(tree, path):
        return JSONResponse({"error": f"Folder '{path}' does not exist."}, status_code=400)

    raw_name = (file.filename or "upload").replace("\\", "/").split("/")[-1]
    if ".." in raw_name or not raw_name.strip():
        return JSONResponse({"error": "Invalid file name."}, status_code=400)

    file_content = file.file.read()
    rel_path = _strip_storage_path(
        (os.path.join(path, raw_name) if path else raw_name).replace("\\", "/"),
    )
    vfs_set_tree(user_id, tree_add_file(tree, path, raw_name))

    is_pdf = raw_name.lower().endswith(".pdf")
    ext = os.path.splitext(raw_name)[1].lower()
    is_image = ext in (".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".gif", ".webp")

    try:
        warnings: list[str] = []
        if is_pdf:
            text, v_err = llm_pipeline.transcribe_document_bytes(file_content, "application/pdf")
            if not (text or "").strip():
                from backend.extract_api import extract_text_from_pdf_bytes
                text = extract_text_from_pdf_bytes(file_content)
                v_err = v_err or "used_pdf_parser_fallback"
            rel_path_txt = rel_path.replace(os.path.splitext(rel_path)[1], ".txt")
            w = _safe_user_document_upsert(user_id, rel_path_txt, text, raw_name)
            if w:
                warnings.append(w)
            ingest_result = ingest_text_for_path(user_id, rel_path, raw_name, text or "")
            return JSONResponse({
                "message": f"File '{raw_name}' uploaded and processed successfully.",
                "path": rel_path,
                "vision_note": v_err,
                "ingestion": ingest_result,
                "warnings": warnings,
            })
        if is_image:
            mime = {
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".png": "image/png",
                ".webp": "image/webp",
                ".gif": "image/gif",
                ".bmp": "image/bmp",
                ".tiff": "image/tiff",
            }.get(ext, "image/jpeg")
            text, v_err = llm_pipeline.transcribe_handwritten_image(file_content, mime)
            if not (text or "").strip():
                from backend.extract_api import extract_text_from_image_bytes

                text = extract_text_from_image_bytes(file_content)
                v_err = v_err or "used_tesseract_fallback"
            base_name = os.path.splitext(raw_name)[0] + ".txt"
            rel_txt = (os.path.join(path, base_name) if path else base_name).replace("\\", "/")
            w = _safe_user_document_upsert(user_id, rel_txt, text or "", raw_name)
            if w:
                warnings.append(w)
            ingest_result = ingest_text_for_path(user_id, rel_txt, raw_name, text or "")
            return JSONResponse({
                "message": f"File '{raw_name}' uploaded and processed successfully.",
                "path": rel_path,
                "vision_note": v_err,
                "ingestion": ingest_result,
                "warnings": warnings,
            })
        if not is_pdf and not is_image:
            text_plain = ""
            low = raw_name.lower()
            if low.endswith((".txt", ".md", ".csv", ".json", ".log")):
                try:
                    text_plain = file_content.decode("utf-8")
                except UnicodeDecodeError:
                    text_plain = file_content.decode("utf-8", errors="replace")
            if (text_plain or "").strip():
                w = _safe_user_document_upsert(user_id, rel_path, text_plain, raw_name)
                if w:
                    warnings.append(w)
                ingest_result = ingest_text_for_path(user_id, rel_path, raw_name, text_plain)
                return JSONResponse({
                    "message": f"File '{raw_name}' uploaded and processed successfully.",
                    "path": rel_path,
                    "ingestion": ingest_result,
                    "warnings": warnings,
                })
        return JSONResponse({
            "message": f"File '{raw_name}' recorded (no server-side binary stored).",
            "path": rel_path,
            "warnings": warnings,
        })
    except Exception as e:
        return JSONResponse({
            "message": f"File '{raw_name}' recorded but processing failed: {str(e)}",
            "path": rel_path,
        }, status_code=500)


@app.post("/chat/upload_ephemeral")
def chat_upload_ephemeral(
    chat_session_id: str = Form(...),
    file: UploadFile = File(...),
    effective: Annotated[tuple[str, bool], Depends(get_effective_user_dep)] = None,
):
    user_id, _ = effective
    sid = (chat_session_id or "").strip()
    if not sid:
        return JSONResponse({"error": "chat_session_id is required."}, status_code=400)
    raw_name = (file.filename or "upload").replace("\\", "/").split("/")[-1]
    if ".." in raw_name or not raw_name.strip():
        return JSONResponse({"error": "Invalid file name."}, status_code=400)
    data = file.file.read()
    ext = os.path.splitext(raw_name)[1].lower()
    is_pdf = ext == ".pdf"
    is_image = ext in (".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".gif", ".webp")
    text = ""
    source = ""
    if is_pdf:
        text, source = llm_pipeline.transcribe_document_bytes(data, "application/pdf")
        if not (text or "").strip():
            from backend.extract_api import extract_text_from_pdf_bytes
            text = extract_text_from_pdf_bytes(data)
            source = source or "used_pdf_parser_fallback"
    elif is_image:
        mime = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".gif": "image/gif",
            ".bmp": "image/bmp",
            ".tiff": "image/tiff",
        }.get(ext, "image/jpeg")
        text, source = llm_pipeline.transcribe_handwritten_image(data, mime)
        if not (text or "").strip():
            from backend.extract_api import extract_text_from_image_bytes
            text = extract_text_from_image_bytes(data)
            source = source or "used_tesseract_fallback"
    else:
        if raw_name.lower().endswith((".txt", ".md", ".csv", ".json", ".log")):
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = data.decode("utf-8", errors="replace")
            source = "plain_text"
    if not (text or "").strip():
        return JSONResponse({"error": "No extractable text found in this file."}, status_code=400)
    _chat_cache_add(
        user_id,
        sid,
        {
            "name": raw_name,
            "text": (text or "").strip()[:120000],
            "source": source or "",
            "uploaded_at": datetime.now().isoformat(),
        },
    )
    return JSONResponse(
        {
            "message": f"Cached '{raw_name}' for this chat session.",
            "chat_session_id": sid,
            "cached_files_count": len(_chat_cache_items(user_id, sid)),
            "text_chars": len((text or "").strip()),
            "extractor_note": source or "",
        }
    )


@app.post("/chat/session/clear")
def clear_chat_session_cache(
    chat_session_id: str = Form(...),
    effective: Annotated[tuple[str, bool], Depends(get_effective_user_dep)] = None,
):
    user_id, _ = effective
    sid = (chat_session_id or "").strip()
    if not sid:
        return JSONResponse({"error": "chat_session_id is required."}, status_code=400)
    deleted = _chat_cache_clear(user_id, sid)
    return JSONResponse({"message": "Chat cache cleared.", "deleted_files": deleted})


@app.delete("/notes")
def delete_notes(
    file: str | None = None,
    files: str | None = None,
    effective: Annotated[tuple[str, bool], Depends(get_effective_user_dep)] = None,
):
    """
    Delete the authenticated user's note chunks from ChromaDB.
    - No query params: delete entire collection (user_{user_id}_notes).
    - file=<path>: delete only chunks for that path.
    - files=<path1>,<path2>: delete only chunks for those paths.
    """
    user_id, _ = effective
    if file:
        paths = [p.strip() for p in [file] if p and p.strip()]
    elif files:
        paths = [p.strip() for p in files.split(",") if p and p.strip()]
    else:
        paths = None
    if paths is not None:
        if not paths:
            return JSONResponse({"error": "No paths provided."}, status_code=400)
        for p in paths:
            if ".." in p or p.startswith("/"):
                return JSONResponse({"error": "Invalid path."}, status_code=400)
        paths = [_strip_storage_path(p) for p in paths]
        deleted = delete_user_notes_by_paths(user_id, paths)
        return JSONResponse({"message": f"Deleted {deleted} chunk(s) for the given file(s).", "deleted_count": deleted})
    deleted_notes = delete_user_notes_collection(user_id)
    deleted_vfs = delete_user_vfs_collection(user_id)
    user_document_delete_many(user_id, path_filter=None)
    return JSONResponse({
        "message": "All notes data deleted." if deleted_notes else "No notes collection found.",
        "deleted_notes": deleted_notes,
        "deleted_vfs": deleted_vfs,
    })


@app.get("/list_tree")
def list_tree(effective: Annotated[tuple[str, bool], Depends(get_effective_user_dep)] = None):
    user_id, _ = effective
    return {"tree": _get_vfs_tree_clean(user_id)}




@app.post("/query_folder")
def query_notes(
    subject: str = Form(""),
    courses: str = Form(""),
    query: str = Form(...),
    highlight: str = Form(""),
    opened_file_path: str = Form(""),
    course_path: str = Form(""),
    include_course_context: bool = Form(True),
    chat_session_id: str = Form(""),
    use_course_notes: bool = Form(False),
    effective: Annotated[tuple[str, bool], Depends(get_effective_user_dep)] = None,
):
    user_id, _ = effective
    q = (query or "").strip()
    if not q:
        return JSONResponse({"error": "Query is empty.", "answer": "", "source_documents": []}, status_code=400)
    tree = _get_vfs_tree_clean(user_id)
    all_paths = [p.replace("\\", "/") for p in flatten_file_paths(tree)]
    all_set = set(all_paths)
    opened = _strip_storage_path(opened_file_path or "")
    course = _strip_storage_path(course_path or "")
    if not course and opened:
        course = opened.split("/", 1)[0] if "/" in opened else opened
    if course and "/" in course:
        course = course.split("/", 1)[0]
    ephemeral = _chat_cache_items(user_id, chat_session_id)

    hl = (highlight or "").strip()
    full_q = f'Regarding this selection:\n"""{hl}"""\n\n{q}' if hl else q
    try:
        col = get_user_collection(user_id)
        embed = _get_query_embedder()
        q_emb = embed.encode([full_q]).tolist()[0]

        def _query_path_candidates(path: str) -> list[str]:
            """For image/text-extracted notes, also try the .txt twin path."""
            p = _strip_storage_path(path or "")
            if not p:
                return []
            out = [p]
            stem, ext = os.path.splitext(p)
            if ext and ext.lower() != ".txt":
                out.append(stem + ".txt")
            # preserve order, dedupe
            return list(dict.fromkeys(out))

        def _cos(a: list[float], b: list[float]) -> float:
            dot = sum((x * y) for x, y in zip(a, b))
            na = (sum((x * x) for x in a) or 1e-9) ** 0.5
            nb = (sum((y * y) for y in b) or 1e-9) ** 0.5
            return float(dot / (na * nb))

        def _split_text(t: str, size: int = 900, overlap: int = 120) -> list[str]:
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

        def _query_path_docs(path: str, n_results: int = 5) -> list[dict]:
            out = []
            seen_local: set[str] = set()
            for p in _query_path_candidates(path):
                res = col.query(
                    query_embeddings=[q_emb],
                    n_results=n_results,
                    where={"path": p},
                    include=["documents", "metadatas", "distances"],
                )
                ids = (res.get("ids") or [[]])[0] if isinstance(res.get("ids"), list) else []
                docs = (res.get("documents") or [[]])[0] if isinstance(res.get("documents"), list) else []
                metas = (res.get("metadatas") or [[]])[0] if isinstance(res.get("metadatas"), list) else []
                dists = (res.get("distances") or [[]])[0] if isinstance(res.get("distances"), list) else []
                for i, cid in enumerate(ids):
                    key = f"{p}|{cid}"
                    if key in seen_local:
                        continue
                    seen_local.add(key)
                    content = (docs[i] if i < len(docs) else "") or ""
                    if not content.strip():
                        continue
                    md = metas[i] if i < len(metas) and isinstance(metas[i], dict) else {}
                    dist = float(dists[i]) if i < len(dists) and dists[i] is not None else 2.0
                    score = 1.0 - dist
                    out.append({"id": cid, "content": content, "metadata": md, "_score": score})
            out.sort(key=lambda d: d.get("_score", -9), reverse=True)
            return out

        # Stage 1: ephemeral chat uploads (highest priority)
        eph_docs: list[dict] = []
        for i, it in enumerate(ephemeral):
            et = str((it or {}).get("text") or "").strip()
            if not et:
                continue
            chunks = _split_text(et, size=900, overlap=140)[:24]
            if not chunks:
                continue
            c_embs = embed.encode(chunks).tolist()
            ranked = sorted(
                [(_cos(q_emb, ce), ch) for ce, ch in zip(c_embs, chunks)],
                key=lambda x: x[0],
                reverse=True,
            )[:3]
            for j, (sc, ch) in enumerate(ranked):
                eph_docs.append(
                    {
                        "id": f"ephemeral|{i}|{j}",
                        "content": ch,
                        "metadata": {
                            "path": f"[chat-upload] {(it or {}).get('name') or 'file'}",
                            "source_file": (it or {}).get("name") or "chat-upload",
                            "ephemeral_chat_upload": 1,
                        },
                        "_score": float(sc),
                    }
                )
        eph_docs.sort(key=lambda d: d.get("_score", -9), reverse=True)
        eph_best = eph_docs[0]["_score"] if eph_docs else -1.0

        # Stage 2: opened file
        open_docs: list[dict] = []
        if opened and opened in all_set:
            open_docs = _query_path_docs(opened, n_results=6)
        open_best = open_docs[0]["_score"] if open_docs else -1.0

        # Stage 3: same course files (excluding opened)
        course_docs: list[dict] = []
        if include_course_context and course:
            prefix = f"{course}/"
            course_paths = [p for p in all_paths if p != opened and p.startswith(prefix)]
            for p in course_paths[:18]:
                course_docs.extend(_query_path_docs(p, n_results=2))
        course_docs.sort(key=lambda d: d.get("_score", -9), reverse=True)
        course_best = course_docs[0]["_score"] if course_docs else -1.0

        # Sequence policy:
        # 1) use ephemeral if relevant enough
        # 2) otherwise opened file
        # 3) otherwise same-course files
        selected: list[dict] = []
        stage = "ephemeral"
        if eph_docs and eph_best >= 0.32:
            selected = eph_docs[:8]
            stage = "ephemeral"
        elif open_docs and open_best >= 0.05:
            selected = open_docs[:8]
            stage = "opened"
        elif course_docs and course_best >= 0.02:
            selected = course_docs[:8]
            stage = "course"
        else:
            # Smart fallback: best available source by embedding score.
            cands = [
                ("ephemeral", eph_best, eph_docs),
                ("opened", open_best, open_docs),
                ("course", course_best, course_docs),
            ]
            cands.sort(key=lambda x: x[1], reverse=True)
            stage, _best, dd = cands[0]
            selected = (dd or [])[:8]

        if not selected:
            return JSONResponse(
                {
                    "error": "No relevant text found in chat uploads, open file, or course notes.",
                    "query": q,
                    "answer": "",
                    "source_documents": [],
                },
                status_code=400,
            )

        context = "\n\n".join(
            [
                f"[{(d.get('metadata') or {}).get('path') or 'chunk'}]\n{(d.get('content') or '')[:1200]}"
                for d in selected
                if (d.get("content") or "").strip()
            ]
        )
        answer, err = llm_pipeline.sarvam_rag_answer(full_q, context)
        if err:
            return JSONResponse(
                {
                    "error": err,
                    "query": q,
                    "answer": "",
                    "source_documents": [],
                },
                status_code=503,
            )
        docs_out = [{k: v for k, v in d.items() if k != "_score"} for d in selected]
        return JSONResponse(
            {
                "query": q,
                "answer": answer or "",
                "source_documents": docs_out,
                "grounded_on_path": opened,
                "ephemeral_files_used": len(ephemeral),
                "selection_stage": stage,
                "score_ephemeral_best": round(float(eph_best), 4),
                "score_opened_best": round(float(open_best), 4),
                "score_course_best": round(float(course_best), 4),
            }
        )
    except Exception as e:
        return JSONResponse(
            {
                "error": f"Query failed: {str(e)}",
                "query": q,
                "answer": "",
                "source_documents": [],
            },
            status_code=500,
        )


@app.get("/file_meta")
def get_file_meta_route(
    effective: Annotated[tuple[str, bool], Depends(get_effective_user_dep)] = None,
):
    user_id, _ = effective
    return {"meta": load_file_meta(user_id)}


@app.post("/file_meta")
def post_file_meta(
    path: str = Form(...),
    is_primary_authority: str = Form("false"),
    doc_type: str = Form(""),
    related_paths: str = Form(""),
    effective: Annotated[tuple[str, bool], Depends(get_effective_user_dep)] = None,
):
    user_id, _ = effective
    path = _strip_storage_path(path)
    primary = str(is_primary_authority).lower() in ("1", "true", "yes", "on")
    rel_list = [
        _strip_storage_path(x.strip().replace("\\", "/"))
        for x in related_paths.split(",")
        if x.strip()
    ]
    entry = set_entry(
        user_id,
        path,
        is_primary_authority=primary,
        doc_type=(doc_type or "other"),
        related_paths=rel_list,
    )
    n = refresh_metadata_for_paths(user_id, chunk_paths_for_file(path))
    return JSONResponse({"path": path, "entry": entry, "chunks_updated": n})


def _doc_text_for_virtual_path(user_id: str, rel_path: str) -> str:
    """Resolve extracted/stored text for a virtual file path."""
    p = _strip_storage_path(rel_path or "")
    if not p:
        return ""
    direct = user_document_get_content(user_id, p)
    if (direct or "").strip():
        return (direct or "").strip()
    stem, ext = os.path.splitext(p)
    if ext.lower() != ".txt":
        txt = user_document_get_content(user_id, stem + ".txt")
        if (txt or "").strip():
            return (txt or "").strip()
    return ""


def _study_text_for_path(user_id: str, rel_path: str) -> str:
    """Prefer Chroma chunk text (same as RAG), fall back to user_documents."""
    p = _strip_storage_path(rel_path or "")
    if not p:
        return ""
    chunk = (user_notes_concat_text_for_path(user_id, p) or "").strip()
    if chunk:
        return chunk
    return (_doc_text_for_virtual_path(user_id, p) or "").strip()


def _build_study_scope_context(
    user_id: str,
    opened_file_path: str,
    course_path: str,
    focus_query: str,
    include_course_context: bool,
) -> tuple[str | None, str | None]:
    """Return context strictly from opened file and optionally same course folder."""
    open_rel = _strip_storage_path(opened_file_path or "")
    if not open_rel:
        return None, "Open a file first. Study generation uses the active file context."

    tree = _get_vfs_tree_clean(user_id)
    all_paths = [p.replace("\\", "/") for p in flatten_file_paths(tree)]
    all_set = set(all_paths)
    if open_rel not in all_set:
        return None, f"Active file '{open_rel}' was not found in your notes."

    root_course = _strip_storage_path(course_path or "")
    if not root_course:
        root_course = open_rel.split("/", 1)[0] if "/" in open_rel else open_rel
    # Root course is only a top-level folder scope, never a file path.
    root_course = root_course.split("/", 1)[0] if root_course else ""

    open_text = (_study_text_for_path(user_id, open_rel) or "").strip()
    if not open_text:
        return None, (
            f"No extracted text or chunks found for '{open_rel}'. "
            "Re-upload the file or wait for ingestion to finish."
        )

    focus = (focus_query or "").strip() or "key ideas for exam"
    parts = [
        f"Focus: {focus}",
        f"Active course folder: {root_course or '(root)'}",
        f"Primary file: {open_rel}",
        "",
        "--- PRIMARY FILE CONTENT ---",
        open_text[:35000],
    ]

    if include_course_context and root_course:
        prefix = root_course + "/"
        extra_paths = [p for p in all_paths if p != open_rel and (p == root_course or p.startswith(prefix))]
        if extra_paths:
            budget = 25000
            extras: list[str] = []
            for p in extra_paths:
                if budget <= 0:
                    break
                t = _study_text_for_path(user_id, p)
                if not t:
                    continue
                chunk = t[: min(len(t), 3500, budget)]
                if not chunk.strip():
                    continue
                extras.append(f"--- COURSE SUPPORTING FILE: {p} ---\n{chunk}")
                budget -= len(chunk)
            if extras:
                parts.append("")
                parts.append("--- OPTIONAL SAME-COURSE SUPPORT ---")
                parts.append("\n\n".join(extras))

    return "\n".join(parts)[:60000], None


@app.post("/study/generate")
def study_generate(
    task: str = Form("flashcards"),
    count: int = Form(5),
    courses: str = Form(""),
    focus_query: str = Form("key ideas for exam"),
    opened_file_path: str = Form(""),
    course_path: str = Form(""),
    include_course_context: bool = Form(True),
    effective: Annotated[tuple[str, bool], Depends(get_effective_user_dep)] = None,
):
    _uid, _ = effective
    course_list = [c.strip() for c in courses.split(",") if c.strip()]
    focus = (focus_query or "").strip() or "key ideas for exam"
    scope = ", ".join(course_list) if course_list else "general"
    ctx, cerr = _build_study_scope_context(
        _uid,
        opened_file_path,
        course_path,
        focus,
        include_course_context,
    )
    if cerr:
        return JSONResponse({"error": cerr, "scope": scope}, status_code=400)
    data, err = llm_pipeline.cheap_study_json(ctx, task, max(1, min(count, 30)))
    if err:
        return JSONResponse({"error": err}, status_code=503)
    return JSONResponse(
        {
            "task": task,
            "items": data,
            "grounded_on_path": _strip_storage_path(opened_file_path or ""),
            "context_chars": len(ctx or ""),
        }
    )


@app.post("/study/mindmap")
def study_summary(
    courses: str = Form(""),
    focus_query: str = Form("main concepts"),
    opened_file_path: str = Form(""),
    course_path: str = Form(""),
    include_course_context: bool = Form(True),
    effective: Annotated[tuple[str, bool], Depends(get_effective_user_dep)] = None,
):
    _uid, _ = effective
    course_list = [c.strip() for c in courses.split(",") if c.strip()]
    focus = (focus_query or "").strip() or "main concepts"
    scope = ", ".join(course_list) if course_list else "general"
    ctx, cerr = _build_study_scope_context(
        _uid,
        opened_file_path,
        course_path,
        focus,
        include_course_context,
    )
    if cerr:
        return JSONResponse({"error": cerr, "scope": scope}, status_code=400)
    summary_text, err = llm_pipeline.topic_summary(ctx)
    if err:
        return JSONResponse({"error": err}, status_code=503)
    return JSONResponse(
        {
            "summary": summary_text,
            "grounded_on_path": _strip_storage_path(opened_file_path or ""),
            "context_chars": len(ctx or ""),
        }
    )


@app.get("/integrations/onenote/status")
def onenote_status(
    effective: Annotated[tuple[str, bool], Depends(get_effective_user_dep)] = None,
):
    user_id, is_guest = effective
    connected = False
    if not is_guest:
        connected = bool(onenote_token_get(user_id))
    return {
        "configured": onenote_sync.is_configured(),
        "connected": connected,
        "requires_sign_in": is_guest,
        "redirect_uri": onenote_sync.redirect_uri(),
    }


@app.get("/integrations/onenote/auth_url")
def onenote_auth_url(
    effective: Annotated[tuple[str, bool], Depends(get_effective_user_dep)] = None,
):
    user_id = _require_signed_in(effective)
    if not onenote_sync.is_configured():
        return JSONResponse(
            {"error": "Microsoft OAuth is not configured on server."},
            status_code=503,
        )
    state = str(uuid.uuid4())
    onenote_oauth_state_create(user_id, state, int(time.time()) + 600)
    return {"auth_url": onenote_sync.build_auth_url(state), "state": state}


@app.get("/integrations/onenote/callback")
def onenote_callback(code: str = "", state: str = "", error: str = "", error_description: str = ""):
    if error:
        msg = error_description or error
        html = (
            "<html><body><h3>OneNote connect failed</h3>"
            f"<p>{msg}</p><p>You can close this tab.</p></body></html>"
        )
        return HTMLResponse(content=html, status_code=400)
    if not code or not state:
        return HTMLResponse(
            content="<html><body><h3>Missing OAuth parameters.</h3><p>Close this tab.</p></body></html>",
            status_code=400,
        )
    user_id = onenote_oauth_state_pop_user_id(state)
    if not user_id:
        return HTMLResponse(
            content="<html><body><h3>OAuth state is invalid or expired.</h3><p>Restart connection.</p></body></html>",
            status_code=400,
        )
    try:
        token_payload = onenote_sync.exchange_code_for_token(code)
        onenote_token_upsert(user_id, token_payload)
        return HTMLResponse(
            content="<html><body><h3>OneNote connected successfully.</h3><p>You can close this tab and return to NoteScanner.</p></body></html>"
        )
    except Exception as e:
        return HTMLResponse(
            content=f"<html><body><h3>Token exchange failed.</h3><p>{str(e)}</p><p>Close this tab.</p></body></html>",
            status_code=500,
        )


@app.post("/integrations/onenote/sync")
def onenote_sync_route(
    max_pages: int = Form(25),
    effective: Annotated[tuple[str, bool], Depends(get_effective_user_dep)] = None,
):
    user_id = _require_signed_in(effective)
    if not onenote_sync.is_configured():
        return JSONResponse({"error": "Microsoft OAuth is not configured on server."}, status_code=503)
    try:
        out = onenote_sync.sync_onenote_pages_to_ingest(user_id, max_pages=max_pages)
        return JSONResponse({"message": "OneNote sync complete.", **out})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
