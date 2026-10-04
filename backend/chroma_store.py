"""
Single-database store using ChromaDB for NoteScanner.

Stores:
- Users, sessions, password-reset tokens: metadata + dummy embeddings
- user_documents: full extracted text per logical path (split into parts) + dummy embedding
- user_{user_id}_notes: chunk text + embeddings + metadata (RAG / search)
- user_{user_id}_vfs: explorer tree + file_meta JSON (structural metadata; not PDF/image binaries)
- user_{user_id}_study: flashcard decks and cards (see study_store.py)

Connection:
- If CHROMA_API_KEY is set → Chroma Cloud (CHROMA_TENANT, CHROMA_DATABASE; optional CHROMA_HOST override via settings if supported)
- Else → self-hosted HttpClient (CHROMA_HOST, CHROMA_PORT, optional CHROMA_SSL=true)

Chroma Cloud limits this module works within: a get/query returns at most 300 records, a write
takes at most 300 records, a document is at most 16 KB, and a record id is at most 128 bytes.
"""
import hashlib
import json
import logging
import os
import threading
import uuid
import time
import chromadb
from chromadb.errors import NotFoundError

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 384  # all-MiniLM-L6-v2
USER_DOCUMENT_MAX_BYTES = int((os.getenv("USER_DOCUMENT_MAX_BYTES") or "2000000").strip() or "2000000")
SESSION_TTL_SECONDS = int(float((os.getenv("SESSION_TTL_DAYS") or "14").strip() or "14") * 86400)
PASSWORD_RESET_TTL_SECONDS = 3600

PAGE_SIZE = 250        # records per get() page (Chroma Cloud max is 300)
WRITE_BATCH = 250      # records per add/upsert/update/delete call (Chroma Cloud max is 300)
TEXT_PART_CHARS = 3500  # ≤ 14 KB even at 4 bytes per character (Chroma Cloud max document is 16 KB)

_global_client = None

_user_locks: dict[str, threading.RLock] = {}
_user_locks_guard = threading.Lock()


def user_lock(user_id: str) -> threading.RLock:
    """Per-user lock so two requests can't overwrite each other's folder tree or file metadata."""
    with _user_locks_guard:
        lock = _user_locks.get(user_id)
        if lock is None:
            lock = _user_locks[user_id] = threading.RLock()
        return lock


def _dummy_embedding():
    return [0.0] * EMBEDDING_DIM


def short_id(prefix: str, *parts: str) -> str:
    """Fixed-length record id (Chroma Cloud ids are limited to 128 bytes; paths can be longer)."""
    digest = hashlib.sha256("\x1f".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:40]
    return f"{prefix}{digest}"


def split_text_parts(text: str, size: int = TEXT_PART_CHARS) -> list[str]:
    text = text or ""
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


def get_global_client():
    global _global_client
    if _global_client is None:
        api_key = (os.getenv("CHROMA_API_KEY") or "").strip()
        if api_key:
            tenant = (os.getenv("CHROMA_TENANT") or "").strip() or None
            database = (os.getenv("CHROMA_DATABASE") or "").strip() or None
            kwargs: dict = {"api_key": api_key}
            if tenant:
                kwargs["tenant"] = tenant
            if database:
                kwargs["database"] = database
            _global_client = chromadb.CloudClient(**kwargs)
        else:
            host = (os.getenv("CHROMA_HOST") or "localhost").strip()
            port_raw = (os.getenv("CHROMA_PORT") or "8100").strip()
            try:
                port = int(port_raw)
            except ValueError:
                port = 8100
            ssl = (os.getenv("CHROMA_SSL") or "").lower() in ("1", "true", "yes")
            _global_client = chromadb.HttpClient(host=host, port=port, ssl=ssl)
    return _global_client


def _get_or_create_collection(name: str):
    client = get_global_client()
    try:
        return client.get_collection(name=name)
    except NotFoundError:
        return client.create_collection(name=name, metadata={"description": "NoteScanner"})


def _get_collection_or_none(name: str):
    """Existing collection, or None if it was never created. Other errors (e.g. no connection) raise."""
    try:
        return get_global_client().get_collection(name=name)
    except NotFoundError:
        return None


def _delete_collection(name: str) -> bool:
    try:
        get_global_client().delete_collection(name=name)
        return True
    except NotFoundError:
        return False


# ---------- Paging and batching helpers (Chroma Cloud limits) ----------
def get_all(col, **kwargs) -> dict:
    """
    col.get() across pages. Returns {"ids": [...], "documents": [...]|None, "metadatas": [...]|None,
    "embeddings": [...]|None}, with lists aligned to ids.
    """
    out: dict = {"ids": [], "documents": None, "metadatas": None, "embeddings": None}
    offset = 0
    while True:
        res = col.get(limit=PAGE_SIZE, offset=offset, **kwargs)
        page_ids = list(res.get("ids") or [])
        out["ids"].extend(page_ids)
        for key in ("documents", "metadatas", "embeddings"):
            vals = res.get(key)
            if vals is not None:
                if out[key] is None:
                    out[key] = []
                out[key].extend(list(vals))
        if len(page_ids) < PAGE_SIZE:
            return out
        offset += PAGE_SIZE


def delete_ids(col, ids: list[str]) -> int:
    ids = list(ids or [])
    for i in range(0, len(ids), WRITE_BATCH):
        col.delete(ids=ids[i : i + WRITE_BATCH])
    return len(ids)


def write_records(col, method: str, ids: list[str], **fields) -> None:
    """add/upsert/update in batches; `fields` are lists aligned with ids (documents, embeddings, metadatas)."""
    fn = getattr(col, method)
    for i in range(0, len(ids), WRITE_BATCH):
        batch = {k: v[i : i + WRITE_BATCH] for k, v in fields.items() if v is not None}
        fn(ids=ids[i : i + WRITE_BATCH], **batch)


def collection_distance_space(col) -> str:
    """Distance type the collection was created with: 'l2' (Chroma default), 'cosine' or 'ip'."""
    space = None
    try:
        cfg = getattr(col, "configuration_json", None) or {}
        for index in ("hnsw", "spann"):
            space = space or ((cfg.get(index) or {}).get("space"))
    except Exception:
        space = None
    if not space:
        space = (getattr(col, "metadata", None) or {}).get("hnsw:space")
    return str(space or "l2").lower()


def distance_to_similarity(distance: float, space: str) -> float:
    """
    Convert a Chroma distance into cosine similarity (-1..1), the same scale used for chat uploads.
    Embeddings are unit length, so squared L2 distance = 2 - 2*cos.
    """
    if space == "l2":
        return 1.0 - distance / 2.0
    return 1.0 - distance  # cosine and ip distances are 1 - cos for unit vectors


# ---------- Per-user note chunks ----------
def get_user_collection(user_id: str):
    """Get or create the per-user RAG collection for note chunks (user_{user_id}_notes)."""
    name = f"user_{user_id}_notes"
    return _get_or_create_collection(name)


def chunk_ids_for_path(path: str, count: int, start: int = 0) -> list[str]:
    base = short_id("c", path)
    return [f"{base}_{start + j}" for j in range(count)]


def join_chunks(chunks: list[str], max_overlap: int = 300) -> str:
    """Join ordered chunks, dropping the text repeated between neighbours (chunks overlap on purpose)."""
    out = ""
    for ch in chunks:
        if not out:
            out = ch
            continue
        overlap = 0
        for k in range(min(max_overlap, len(ch), len(out)), 0, -1):
            if out.endswith(ch[:k]):
                overlap = k
                break
        if overlap:
            out += ch[overlap:]
        else:
            out += "\n\n" + ch
    return out


def user_notes_concat_text_for_path(user_id: str, path: str) -> str:
    """
    Full text of a file rebuilt from its search chunks, in order, without the repeated overlap.
    """
    p = (path or "").replace("\\", "/").strip()
    if not p:
        return ""
    col = _get_collection_or_none(f"user_{user_id}_notes")
    if col is None:
        return ""
    res = get_all(col, where={"path": p}, include=["documents", "metadatas"])
    docs = res.get("documents") or []
    metas = res.get("metadatas") or []
    pairs: list[tuple[int, str]] = []
    for i, doc in enumerate(docs):
        meta = metas[i] if i < len(metas) else {}
        idx = i
        if isinstance(meta, dict) and meta.get("chunk_index") is not None:
            try:
                idx = int(meta["chunk_index"])
            except (TypeError, ValueError):
                idx = i
        text = (doc or "").strip()
        if text:
            pairs.append((idx, text))
    pairs.sort(key=lambda x: x[0])
    return join_chunks([t for _, t in pairs])


def delete_user_notes_collection(user_id: str) -> bool:
    """Delete the entire per-user RAG collection. Returns True if deleted, False if it did not exist."""
    return _delete_collection(f"user_{user_id}_notes")


def delete_user_notes_by_paths(user_id: str, paths: list[str]) -> int:
    """Delete chunks whose metadata path is in paths. Returns number of ids deleted."""
    if not paths:
        return 0
    col = _get_collection_or_none(f"user_{user_id}_notes")
    if col is None:
        return 0
    deleted = 0
    for p in paths:
        ids = get_all(col, where={"path": p}, include=[])["ids"]
        deleted += delete_ids(col, ids)
    return deleted


# ---------- Per-user folder tree + file metadata ----------
def get_user_vfs_collection(user_id: str):
    """Virtual folder tree + file metadata (JSON documents). Binaries are not stored."""
    name = f"user_{user_id}_vfs"
    return _get_or_create_collection(name)


VFS_TREE_DOC_ID = "vfs_tree"
VFS_FILE_META_DOC_ID = "vfs_file_meta"
VFS_STORAGE_VERSION_DOC_ID = "vfs_storage_version"


def _read_parts(col, doc_id: str) -> str | None:
    """
    Read a JSON document stored as a manifest record (`doc_id`) plus parts (`doc_id#<version>#<k>`).
    Older single-record documents (no `parts` in the manifest) are read directly. None if missing.
    """
    for _attempt in range(2):
        res = col.get(ids=[doc_id], include=["documents", "metadatas"])
        if not res.get("ids"):
            return None
        meta = (res.get("metadatas") or [{}])[0] or {}
        parts = int(meta.get("parts") or 0)
        if not parts:
            return (res.get("documents") or [""])[0] or ""
        version = str(meta.get("version") or "")
        part_ids = [f"{doc_id}#{version}#{k}" for k in range(parts)]
        got = col.get(ids=part_ids, include=["documents"])
        by_id = dict(zip(got.get("ids") or [], got.get("documents") or []))
        if all(pid in by_id for pid in part_ids):
            return "".join(by_id[pid] or "" for pid in part_ids)
        # A writer replaced the parts while we were reading; read the new manifest once more.
    raise RuntimeError(f"Stored document {doc_id} is incomplete")


def _write_parts(col, doc_id: str, raw: str, kind: str) -> None:
    """
    Write parts first under a new version, then switch the manifest to it in one upsert, then
    remove the previous version's parts. A failure at any step leaves the previous version intact.
    """
    old = col.get(ids=[doc_id], include=["metadatas"])
    old_meta = ((old.get("metadatas") or [{}])[0] or {}) if old.get("ids") else {}
    version = uuid.uuid4().hex[:12]
    pieces = split_text_parts(raw)
    part_ids = [f"{doc_id}#{version}#{k}" for k in range(len(pieces))]
    write_records(
        col,
        "upsert",
        part_ids,
        documents=pieces,
        embeddings=[_dummy_embedding()] * len(pieces),
        metadatas=[{"kind": f"{kind}_part"}] * len(pieces),
    )
    col.upsert(
        ids=[doc_id],
        documents=[""],
        embeddings=[_dummy_embedding()],
        metadatas=[{"kind": kind, "parts": len(pieces), "version": version}],
    )
    old_parts = int(old_meta.get("parts") or 0)
    old_version = str(old_meta.get("version") or "")
    if old_parts and old_version and old_version != version:
        try:
            delete_ids(col, [f"{doc_id}#{old_version}#{k}" for k in range(old_parts)])
        except Exception:
            logger.warning("Could not remove old parts of %s", doc_id, exc_info=True)


def _vfs_read_json(user_id: str, doc_id: str, default):
    """
    Read one JSON document from the user's vfs collection.
    Missing collection/document -> default. Connection or parse errors raise, so callers never
    mistake a failed read for an empty tree and then overwrite the real one.
    """
    col = _get_collection_or_none(f"user_{user_id}_vfs")
    if col is None:
        return default
    raw = _read_parts(col, doc_id)
    if not raw:
        return default
    data = json.loads(raw)
    return data if isinstance(data, type(default)) else default


def _vfs_write_json(user_id: str, doc_id: str, data, kind: str) -> None:
    _write_parts(get_user_vfs_collection(user_id), doc_id, json.dumps(data, ensure_ascii=False), kind)


def vfs_get_tree(user_id: str) -> list:
    return _vfs_read_json(user_id, VFS_TREE_DOC_ID, [])


def vfs_set_tree(user_id: str, tree: list) -> None:
    _vfs_write_json(user_id, VFS_TREE_DOC_ID, tree, "vfs_tree")


def vfs_get_file_meta_dict(user_id: str) -> dict:
    return _vfs_read_json(user_id, VFS_FILE_META_DOC_ID, {})


def vfs_set_file_meta_dict(user_id: str, meta: dict) -> None:
    _vfs_write_json(user_id, VFS_FILE_META_DOC_ID, meta, "vfs_file_meta")


def vfs_get_storage_version(user_id: str) -> int:
    data = _vfs_read_json(user_id, VFS_STORAGE_VERSION_DOC_ID, {})
    try:
        return int(data.get("version") or 0)
    except (TypeError, ValueError):
        return 0


def vfs_set_storage_version(user_id: str, version: int) -> None:
    _vfs_write_json(user_id, VFS_STORAGE_VERSION_DOC_ID, {"version": int(version)}, "vfs_storage_version")


def delete_user_vfs_collection(user_id: str) -> bool:
    return _delete_collection(f"user_{user_id}_vfs")


# ---------- Users ----------
def get_users_collection():
    return _get_or_create_collection("users")


def user_exists_by_email(email: str) -> bool:
    col = get_users_collection()
    res = col.get(where={"email": email.lower()}, include=[])
    return len(res["ids"]) > 0


def user_create(email: str, hashed_password: str, name: str, **extra) -> str:
    user_id = str(uuid.uuid4())
    col = get_users_collection()
    meta = {
        "email": email.lower(),
        "hashed_password": hashed_password,
        "name": name or email.split("@")[0],
        "created_at": int(time.time()),
        **extra,
    }
    col.add(ids=[user_id], documents=[""], metadatas=[meta], embeddings=[_dummy_embedding()])
    return user_id


def _user_from_result(res, with_password: bool) -> dict | None:
    if not res["ids"]:
        return None
    meta = res["metadatas"][0] or {}
    user = {
        "user_id": res["ids"][0],
        "email": meta.get("email"),
        "name": meta.get("name"),
        # Preferences and bookkeeping live in the user's metadata under pref_* / other keys.
        "meta": {k: v for k, v in meta.items() if k != "hashed_password"},
    }
    if with_password:
        user["hashed_password"] = meta.get("hashed_password")
    return user


def user_get_by_email(email: str) -> dict | None:
    col = get_users_collection()
    return _user_from_result(col.get(where={"email": email.lower()}), with_password=True)


def user_get_by_id(user_id: str, with_password: bool = False) -> dict | None:
    """User record, or None if it doesn't exist. Database errors raise."""
    col = get_users_collection()
    return _user_from_result(col.get(ids=[user_id]), with_password=with_password)


def user_update_metadata(user_id: str, **fields) -> dict:
    """Set some metadata fields on a user record (other fields are kept). Returns the new metadata."""
    col = get_users_collection()
    res = col.get(ids=[user_id], include=["metadatas"])
    if not res["ids"]:
        raise ValueError("User not found")
    meta = {**(res["metadatas"][0] or {}), **fields}
    col.update(ids=[user_id], metadatas=[meta])
    return meta


def user_update_password(user_id: str, hashed_password: str) -> None:
    user_update_metadata(user_id, hashed_password=hashed_password)


def users_where(where: dict) -> list[dict]:
    """Users matching a metadata filter, e.g. {"pref_reminders": 1} (password hashes left out)."""
    res = get_all(get_users_collection(), where=where, include=["metadatas"])
    out = []
    for uid, meta in zip(res["ids"], res.get("metadatas") or []):
        meta = {k: v for k, v in (meta or {}).items() if k != "hashed_password"}
        out.append({"user_id": uid, "email": meta.get("email"), "name": meta.get("name"), "meta": meta})
    return out


def user_delete(user_id: str) -> None:
    get_users_collection().delete(ids=[user_id])


# ---------- Sessions ----------
def get_sessions_collection():
    return _get_or_create_collection("sessions")


def _session_delete_expired_for_user(col, user_id: str, now: int) -> None:
    try:
        res = get_all(col, where={"user_id": user_id}, include=["metadatas"])
        stale = [
            sid
            for sid, meta in zip(res["ids"], res.get("metadatas") or [])
            if int((meta or {}).get("expires_at") or 0) <= now
        ]
        delete_ids(col, stale)
    except Exception:
        logger.warning("Could not clean up expired sessions for %s", user_id, exc_info=True)


def session_create(user_id: str) -> str:
    session_id = str(uuid.uuid4())
    now = int(time.time())
    col = get_sessions_collection()
    _session_delete_expired_for_user(col, user_id, now)
    col.add(
        ids=[session_id],
        documents=[""],
        metadatas=[{"user_id": user_id, "created_at": now, "expires_at": now + SESSION_TTL_SECONDS}],
        embeddings=[_dummy_embedding()],
    )
    return session_id


def session_get_user_id(session_id: str) -> str | None:
    """User id for a live session, or None if unknown/expired. Database errors raise."""
    if not session_id:
        return None
    col = get_sessions_collection()
    res = col.get(ids=[session_id], include=["metadatas"])
    if not res["ids"]:
        return None
    meta = res["metadatas"][0] or {}
    # Sessions created before expiry existed have no expires_at and are treated as expired.
    if int(meta.get("expires_at") or 0) <= int(time.time()):
        col.delete(ids=[session_id])
        return None
    return meta.get("user_id")


def session_delete(session_id: str) -> None:
    if not session_id:
        return
    get_sessions_collection().delete(ids=[session_id])


def session_delete_all_for_user(user_id: str, keep_session_id: str | None = None) -> int:
    """End every session of a user (e.g. after a password change), optionally keeping one."""
    col = get_sessions_collection()
    ids = [sid for sid in get_all(col, where={"user_id": user_id}, include=[])["ids"] if sid != keep_session_id]
    return delete_ids(col, ids)


# ---------- Password reset tokens (only a hash of the token is stored) ----------
def get_password_resets_collection():
    return _get_or_create_collection("password_resets")


def password_reset_create(user_id: str, token: str) -> None:
    col = get_password_resets_collection()
    delete_ids(col, get_all(col, where={"user_id": user_id}, include=[])["ids"])
    col.add(
        ids=[short_id("r", token)],
        documents=[""],
        metadatas=[{"user_id": user_id, "expires_at": int(time.time()) + PASSWORD_RESET_TTL_SECONDS}],
        embeddings=[_dummy_embedding()],
    )


def password_reset_pop_user_id(token: str) -> str | None:
    """Single use: the token is deleted whether or not it is still valid."""
    if not token:
        return None
    col = get_password_resets_collection()
    rid = short_id("r", token)
    res = col.get(ids=[rid], include=["metadatas"])
    if not res["ids"]:
        return None
    col.delete(ids=[rid])
    meta = res["metadatas"][0] or {}
    if int(meta.get("expires_at") or 0) <= int(time.time()):
        return None
    return meta.get("user_id") or None


def password_reset_delete_for_user(user_id: str) -> None:
    col = get_password_resets_collection()
    delete_ids(col, get_all(col, where={"user_id": user_id}, include=[])["ids"])


# ---------- User documents (full extracted text, stored in parts) ----------
def get_user_documents_collection():
    return _get_or_create_collection("user_documents")


def _doc_where(user_id: str, path: str) -> dict:
    return {"$and": [{"user_id": str(user_id)}, {"path": str(path)}]}


def _doc_records(user_id: str, path: str, include: list[str]) -> dict:
    """All records for (user, path): new multi-part records and older single records alike."""
    return get_all(get_user_documents_collection(), where=_doc_where(user_id, path), include=include)


def _truncate_utf8_bytes(text: str, max_bytes: int) -> tuple[str, int]:
    raw = (text or "").encode("utf-8")
    if len(raw) <= max_bytes:
        return (text or ""), len(raw)
    cut = raw[: max(0, max_bytes)]
    while cut:
        try:
            return cut.decode("utf-8"), len(raw)
        except UnicodeDecodeError:
            cut = cut[:-1]
    return "", len(raw)


def user_document_upsert(user_id: str, path: str, content: str, original_filename: str):
    """Store extracted text for a path (original PDFs/images stay on the client)."""
    col = get_user_documents_collection()
    trimmed, full_bytes = _truncate_utf8_bytes(content or "", USER_DOCUMENT_MAX_BYTES)
    stored_bytes = len((trimmed or "").encode("utf-8"))
    pieces = split_text_parts(trimmed)
    base = short_id("d", user_id, path)
    ids = [f"{base}_{k}" for k in range(len(pieces))]
    meta = {
        "user_id": str(user_id),
        "path": str(path or ""),
        "original_filename": str(original_filename or ""),
        "truncated": int(stored_bytes < full_bytes),
        "content_bytes": int(full_bytes),
        "stored_bytes": int(stored_bytes),
        "parts": len(pieces),
    }
    stale = [rid for rid in _doc_records(user_id, path, include=[])["ids"] if rid not in set(ids)]
    write_records(
        col,
        "upsert",
        ids,
        documents=pieces,
        embeddings=[_dummy_embedding()] * len(pieces),
        metadatas=[{**meta, "part": k} for k in range(len(pieces))],
    )
    delete_ids(col, stale)


def user_document_get(user_id: str, path: str) -> dict | None:
    """{"content", "truncated", "original_filename"} for a path, or None if nothing is stored."""
    res = _doc_records(user_id, path, include=["documents", "metadatas"])
    if not res["ids"]:
        return None
    rows = sorted(
        zip(res.get("documents") or [], res.get("metadatas") or []),
        key=lambda r: int((r[1] or {}).get("part") or 0),
    )
    first = rows[0][1] or {}
    return {
        "content": "".join(doc or "" for doc, _ in rows),
        "truncated": bool(int(first.get("truncated") or 0)),
        "original_filename": first.get("original_filename") or "",
    }


def user_document_get_content(user_id: str, path: str) -> str | None:
    doc = user_document_get(user_id, path)
    return doc["content"] if doc else None


def user_document_exists(user_id: str, path: str) -> bool:
    """True if extracted text is stored for this path. Database errors raise."""
    return bool(_doc_records(user_id, path, include=[])["ids"])


def user_document_delete_by_path(user_id: str, path: str):
    col = get_user_documents_collection()
    delete_ids(col, _doc_records(user_id, path, include=[])["ids"])


def user_document_delete_many(user_id: str, path_filter=None):
    """Delete documents for user_id. path_filter: if callable(path) -> bool, only delete matching paths."""
    col = get_user_documents_collection()
    res = get_all(col, where={"user_id": str(user_id)}, include=["metadatas"])
    ids_to_delete = [
        doc_id
        for doc_id, meta in zip(res["ids"], res.get("metadatas") or [])
        if path_filter is None or path_filter((meta or {}).get("path", ""))
    ]
    delete_ids(col, ids_to_delete)


def user_documents_relocate_prefix(user_id: str, old_prefix: str, new_prefix: str) -> int:
    """Rename stored document paths under old_prefix to new_prefix (longest paths first)."""
    col = get_user_documents_collection()
    res = get_all(col, where={"user_id": str(user_id)}, include=["metadatas"])
    paths: set[str] = set()
    for m in res.get("metadatas") or []:
        p = ((m or {}).get("path") or "").replace("\\", "/")
        if p == old_prefix or p.startswith(old_prefix + "/"):
            paths.add(p)
    pairs = sorted(((p, new_prefix + p[len(old_prefix) :]) for p in paths), key=lambda x: -len(x[0]))
    n = 0
    for op, np in pairs:
        if op != np and user_document_rename_path(user_id, op, np):
            n += 1
    return n


def user_document_rename_path(user_id: str, old_path: str, new_path: str) -> bool:
    """Move stored extracted text from old_path to new_path."""
    doc = user_document_get(user_id, old_path)
    if doc is None:
        return False
    ofn = doc["original_filename"] or os.path.basename(new_path.replace("\\", "/"))
    user_document_upsert(user_id, new_path, doc["content"], ofn)
    user_document_delete_by_path(user_id, old_path)
    return True


# ---------- OneNote OAuth tokens/state ----------
def get_onenote_tokens_collection():
    return _get_or_create_collection("onenote_tokens")


def get_onenote_oauth_state_collection():
    return _get_or_create_collection("onenote_oauth_states")


def _oauth_state_id(state: str) -> str:
    return f"state|{state}"


def onenote_oauth_state_create(user_id: str, state: str, expires_at_epoch: int) -> None:
    col = get_onenote_oauth_state_collection()
    col.upsert(
        ids=[_oauth_state_id(state)],
        documents=[""],
        metadatas=[{"user_id": str(user_id), "expires_at": int(expires_at_epoch)}],
        embeddings=[_dummy_embedding()],
    )


def onenote_oauth_state_pop_user_id(state: str) -> str | None:
    col = get_onenote_oauth_state_collection()
    sid = _oauth_state_id(state)
    try:
        res = col.get(ids=[sid], include=["metadatas"])
        if not res.get("ids"):
            return None
        meta = (res.get("metadatas") or [{}])[0] or {}
        try:
            exp = int(meta.get("expires_at") or 0)
        except Exception:
            exp = 0
        user_id = str(meta.get("user_id") or "")
        col.delete(ids=[sid])
        if not user_id:
            return None
        if exp and exp < int(time.time()):
            return None
        return user_id
    except Exception:
        logger.warning("OneNote OAuth state lookup failed", exc_info=True)
        return None


def onenote_token_upsert(user_id: str, token_payload: dict) -> None:
    col = get_onenote_tokens_collection()
    uid = str(user_id)
    try:
        raw = json.dumps(token_payload or {}, ensure_ascii=False)
    except Exception:
        raw = "{}"
    expires_at = 0
    try:
        expires_at = int((token_payload or {}).get("expires_at") or 0)
    except Exception:
        expires_at = 0
    col.upsert(
        ids=[uid],
        documents=[raw],
        metadatas=[{"user_id": uid, "expires_at": expires_at}],
        embeddings=[_dummy_embedding()],
    )


def onenote_token_get(user_id: str) -> dict | None:
    col = get_onenote_tokens_collection()
    uid = str(user_id)
    try:
        res = col.get(ids=[uid], include=["documents"])
        if not res.get("ids"):
            return None
        raw = (res.get("documents") or ["{}"])[0] or "{}"
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        logger.warning("OneNote token lookup failed for %s", user_id, exc_info=True)
        return None


def onenote_token_delete(user_id: str) -> None:
    get_onenote_tokens_collection().delete(ids=[str(user_id)])
