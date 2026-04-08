"""
Single-database store using ChromaDB for NoteScanner.

Stores:
- Users, sessions: metadata + dummy embeddings
- user_documents: full extracted text per logical path + metadata + dummy embedding
- user_{user_id}_notes: chunk text + embeddings + metadata (RAG / search)
- user_{user_id}_vfs: explorer tree + file_meta JSON (structural metadata; not PDF/image binaries)

Connection:
- If CHROMA_API_KEY is set → Chroma Cloud (CHROMA_TENANT, CHROMA_DATABASE; optional CHROMA_HOST override via settings if supported)
- Else → self-hosted HttpClient (CHROMA_HOST, CHROMA_PORT, optional CHROMA_SSL=true)
"""
import json
import os
import uuid
import time
import chromadb

EMBEDDING_DIM = 384  # all-MiniLM-L6-v2
USER_DOCUMENT_MAX_BYTES = int((os.getenv("USER_DOCUMENT_MAX_BYTES") or "12000").strip() or "12000")

_global_client = None


def _dummy_embedding():
    return [0.0] * EMBEDDING_DIM


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
    except Exception:
        return client.create_collection(name=name, metadata={"description": "NoteScanner"})


def get_user_collection(user_id: str):
    """Get or create the per-user RAG collection for note chunks (user_{user_id}_notes)."""
    name = f"user_{user_id}_notes"
    return _get_or_create_collection(name)


def user_notes_concat_text_for_path(user_id: str, path: str) -> str:
    """
    Concatenate all chunk documents for a virtual file path (metadata.path from ingest).
    PDFs/images are ingested under the original path (e.g. Maths/L3.1_Part1.pdf), not only user_documents.
    """
    p = (path or "").replace("\\", "/").strip()
    if not p:
        return ""
    col = get_user_collection(user_id)
    try:
        res = col.get(where={"path": p}, include=["documents", "metadatas"])
    except Exception:
        return ""
    docs = res.get("documents") or []
    metas = res.get("metadatas") or []
    if not docs:
        return ""
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
    return "\n\n".join(t for _, t in pairs)


def delete_user_notes_collection(user_id: str) -> bool:
    """Delete the entire per-user RAG collection. Returns True if deleted, False if it did not exist."""
    client = get_global_client()
    name = f"user_{user_id}_notes"
    try:
        client.delete_collection(name=name)
        return True
    except Exception:
        return False


def get_user_vfs_collection(user_id: str):
    """Virtual folder tree + file metadata (JSON documents). Binaries are not stored."""
    name = f"user_{user_id}_vfs"
    return _get_or_create_collection(name)


VFS_TREE_DOC_ID = "vfs_tree"
VFS_FILE_META_DOC_ID = "vfs_file_meta"


def vfs_get_tree(user_id: str) -> list:
    col = get_user_vfs_collection(user_id)
    try:
        res = col.get(ids=[VFS_TREE_DOC_ID], include=["documents"])
        if not res.get("ids"):
            return []
        raw = (res.get("documents") or [""])[0] or "[]"
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def vfs_set_tree(user_id: str, tree: list) -> None:
    col = get_user_vfs_collection(user_id)
    doc = json.dumps(tree, ensure_ascii=False)
    try:
        col.delete(ids=[VFS_TREE_DOC_ID])
    except Exception:
        pass
    col.add(
        ids=[VFS_TREE_DOC_ID],
        documents=[doc],
        embeddings=[_dummy_embedding()],
        metadatas=[{"kind": "vfs_tree"}],
    )


def vfs_get_file_meta_dict(user_id: str) -> dict:
    col = get_user_vfs_collection(user_id)
    try:
        res = col.get(ids=[VFS_FILE_META_DOC_ID], include=["documents"])
        if not res.get("ids"):
            return {}
        raw = (res.get("documents") or [""])[0] or "{}"
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def vfs_set_file_meta_dict(user_id: str, meta: dict) -> None:
    col = get_user_vfs_collection(user_id)
    doc = json.dumps(meta, ensure_ascii=False)
    try:
        col.delete(ids=[VFS_FILE_META_DOC_ID])
    except Exception:
        pass
    col.add(
        ids=[VFS_FILE_META_DOC_ID],
        documents=[doc],
        embeddings=[_dummy_embedding()],
        metadatas=[{"kind": "vfs_file_meta"}],
    )


def delete_user_vfs_collection(user_id: str) -> bool:
    client = get_global_client()
    name = f"user_{user_id}_vfs"
    try:
        client.delete_collection(name=name)
        return True
    except Exception:
        return False


def delete_user_notes_by_paths(user_id: str, paths: list[str]) -> int:
    """Delete chunks whose metadata path is in paths. Returns number of ids deleted."""
    if not paths:
        return 0
    col = get_user_collection(user_id)
    ids_to_drop = []
    for p in paths:
        try:
            res = col.get(where={"path": p}, include=[])
            ids_to_drop.extend(res.get("ids") or [])
        except Exception:
            continue
    if ids_to_drop:
        col.delete(ids=ids_to_drop)
    return len(ids_to_drop)


# ---------- Users ----------
def get_users_collection():
    return _get_or_create_collection("users")


def user_exists_by_email(email: str) -> bool:
    col = get_users_collection()
    res = col.get(where={"email": email.lower()})
    return len(res["ids"]) > 0


def user_create(email: str, hashed_password: str, name: str) -> str:
    user_id = str(uuid.uuid4())
    col = get_users_collection()
    col.add(
        ids=[user_id],
        documents=[""],
        metadatas=[{"email": email.lower(), "hashed_password": hashed_password, "name": name or email.split("@")[0]}],
        embeddings=[_dummy_embedding()],
    )
    return user_id


def user_get_by_email(email: str) -> dict | None:
    col = get_users_collection()
    res = col.get(where={"email": email.lower()})
    if not res["ids"]:
        return None
    return {
        "user_id": res["ids"][0],
        "email": res["metadatas"][0].get("email"),
        "hashed_password": res["metadatas"][0].get("hashed_password"),
        "name": res["metadatas"][0].get("name"),
    }


def user_get_by_id(user_id: str) -> dict | None:
    col = get_users_collection()
    try:
        res = col.get(ids=[user_id])
        if not res["ids"]:
            return None
        return {
            "user_id": res["ids"][0],
            "email": res["metadatas"][0].get("email"),
            "name": res["metadatas"][0].get("name"),
        }
    except Exception:
        return None


# ---------- Sessions ----------
def get_sessions_collection():
    return _get_or_create_collection("sessions")


def session_create(user_id: str) -> str:
    session_id = str(uuid.uuid4())
    col = get_sessions_collection()
    col.add(
        ids=[session_id],
        documents=[""],
        metadatas=[{"user_id": user_id}],
        embeddings=[_dummy_embedding()],
    )
    return session_id


def session_get_user_id(session_id: str) -> str | None:
    if not session_id:
        return None
    col = get_sessions_collection()
    try:
        res = col.get(ids=[session_id])
        if not res["ids"]:
            return None
        return res["metadatas"][0].get("user_id")
    except Exception:
        return None


# ---------- User documents (extracted text from PDFs) ----------
def get_user_documents_collection():
    return _get_or_create_collection("user_documents")


def _doc_id(user_id: str, path: str) -> str:
    # ChromaDB ids: no pipe in path to avoid collision
    safe_path = path.replace("|", "\x00")
    return f"{user_id}|{safe_path}"


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
    """Store extracted text for a path with metadata (original PDFs/images stay on the client)."""
    col = get_user_documents_collection()
    doc_id = _doc_id(user_id, path)
    trimmed, full_bytes = _truncate_utf8_bytes(content or "", USER_DOCUMENT_MAX_BYTES)
    stored_bytes = len((trimmed or "").encode("utf-8"))
    truncated = int(stored_bytes < full_bytes)
    col.upsert(
        ids=[doc_id],
        documents=[trimmed],
        metadatas=[
            {
                "user_id": str(user_id),
                "path": str(path or ""),
                "original_filename": str(original_filename or ""),
                "truncated": truncated,
                "content_bytes": int(full_bytes),
                "stored_bytes": int(stored_bytes),
            }
        ],
        embeddings=[_dummy_embedding()],
    )


def user_document_delete_many(user_id: str, path_filter=None):
    """Delete documents for user_id. path_filter: if callable(path) -> bool, only delete matching paths."""
    col = get_user_documents_collection()
    res = col.get(where={"user_id": user_id})
    if not res["ids"]:
        return
    ids_to_delete = []
    for i, doc_id in enumerate(res["ids"]):
        path = res["metadatas"][i].get("path", "")
        if path_filter is None or path_filter(path):
            ids_to_delete.append(doc_id)
    if ids_to_delete:
        col.delete(ids=ids_to_delete)


def user_document_delete_by_path(user_id: str, path: str):
    doc_id = _doc_id(user_id, path)
    col = get_user_documents_collection()
    try:
        col.delete(ids=[doc_id])
    except Exception:
        pass


def user_document_get_content(user_id: str, path: str) -> str | None:
    col = get_user_documents_collection()
    doc_id = _doc_id(user_id, path)
    try:
        res = col.get(ids=[doc_id], include=["documents"])
        if not res.get("ids"):
            return None
        docs = res.get("documents") or []
        return docs[0] if docs else None
    except Exception:
        return None


def user_documents_relocate_prefix(user_id: str, old_prefix: str, new_prefix: str) -> int:
    """Rename stored document paths under old_prefix to new_prefix (longest paths first)."""
    col = get_user_documents_collection()
    res = col.get(where={"user_id": user_id}, include=["metadatas"])
    metas = res.get("metadatas") or []
    pairs: list[tuple[str, str]] = []
    for m in metas:
        p = ((m or {}).get("path") or "").replace("\\", "/")
        if p == old_prefix:
            pairs.append((p, new_prefix))
        elif p.startswith(old_prefix + "/"):
            pairs.append((p, new_prefix + p[len(old_prefix) :]))
    pairs.sort(key=lambda x: -len(x[0]))
    n = 0
    for op, np in pairs:
        if op != np and user_document_rename_path(user_id, op, np):
            n += 1
    return n


def user_document_rename_path(user_id: str, old_path: str, new_path: str) -> bool:
    """Move stored extracted text from old_path to new_path."""
    col = get_user_documents_collection()
    oid = _doc_id(user_id, old_path)
    try:
        res = col.get(ids=[oid], include=["documents", "metadatas"])
        if not res.get("ids"):
            return False
        content = (res.get("documents") or [""])[0] or ""
        meta = res.get("metadatas")[0] or {}
        ofn = meta.get("original_filename") or os.path.basename(new_path.replace("\\", "/"))
        col.delete(ids=[oid])
        user_document_upsert(user_id, new_path, content, ofn)
        return True
    except Exception:
        return False


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
        return None
