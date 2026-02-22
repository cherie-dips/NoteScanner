"""
Single-database store using ChromaDB for NoteScanner.

Stores:
- User login details (users collection)
- Sessions (sessions collection)
- Extracted text from PDFs (user_documents collection)
- Per-user RAG note chunks (user_{user_id}_notes collections)

Uses a centralized ChromaDB server via HttpClient (CHROMA_HOST, CHROMA_PORT).
"""
import os
import uuid
import chromadb

CHROMA_HOST = os.getenv("CHROMA_HOST", "localhost")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", "8100"))
EMBEDDING_DIM = 384  # all-MiniLM-L6-v2

_global_client = None


def _dummy_embedding():
    return [0.0] * EMBEDDING_DIM


def get_global_client():
    global _global_client
    if _global_client is None:
        _global_client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
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


def delete_user_notes_collection(user_id: str) -> bool:
    """Delete the entire per-user RAG collection. Returns True if deleted, False if it did not exist."""
    client = get_global_client()
    name = f"user_{user_id}_notes"
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


def user_document_upsert(user_id: str, path: str, content: str, original_filename: str):
    col = get_user_documents_collection()
    doc_id = _doc_id(user_id, path)
    col.upsert(
        ids=[doc_id],
        documents=[content],
        metadatas=[{"user_id": user_id, "path": path, "original_filename": original_filename}],
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
