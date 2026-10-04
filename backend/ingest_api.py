"""Ingest text into ChromaDB using the centralized client and per-user collection (user_{user_id}_notes)."""
import logging
import os
import threading

from sentence_transformers import SentenceTransformer
from langchain_text_splitters import RecursiveCharacterTextSplitter

from backend.chroma_store import (
    chunk_ids_for_path,
    delete_ids,
    get_all,
    get_user_collection,
    write_records,
)
from backend.file_meta import course_id_from_path, get_entry
from backend.pages import page_at

CHUNK_SIZE = 750
CHUNK_OVERLAP = 100
BATCH_SIZE = 50
EMBED_MODEL_NAME = "all-MiniLM-L6-v2"

logger = logging.getLogger(__name__)

_embedder = None
_embedder_lock = threading.Lock()


def get_embedder() -> SentenceTransformer:
    """Load the embedding model once per process and reuse it (loading takes seconds)."""
    global _embedder
    if _embedder is None:
        with _embedder_lock:
            if _embedder is None:
                _embedder = SentenceTransformer(EMBED_MODEL_NAME)
    return _embedder


def embed_texts(texts: list[str]):
    """Unit-length embeddings (numpy array, one row per text)."""
    return get_embedder().encode(list(texts), normalize_embeddings=True)


def _chunk_text(text: str):
    splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    return splitter.split_text(text)


def _chunk_text_with_pages(text: str, page_starts) -> tuple[list[str], list[int | None]]:
    """Chunks plus the page each chunk starts on (None when the text has no page information)."""
    if not page_starts:
        chunks = _chunk_text(text)
        return chunks, [None] * len(chunks)
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP, add_start_index=True
    )
    docs = splitter.create_documents([text])
    return (
        [d.page_content for d in docs],
        [page_at(int(d.metadata.get("start_index") or 0), page_starts) for d in docs],
    )

# Chunk metadata that belongs to the chunk itself (kept when a file is moved or re-labelled).
_CHUNK_KEYS = ("chunk_index", "page")


def _base_metadata_user(user_id: str, path: str, source_file: str) -> dict:
    fm = get_entry(user_id, path)
    # Chroma metadata: str, int, float, bool — use int flags for broad server/Cloud compatibility.
    return {
        "user_id": str(user_id),
        "path": str(path or ""),
        "source_file": str(source_file or ""),
        "course_id": str(course_id_from_path(path)),
        "is_primary_authority": int(bool(fm.get("is_primary_authority"))),
        "doc_type": str(fm.get("doc_type") or "other"),
    }


def _add_chunks_to_collection(collection, chunks: list, path: str, source_file: str, user_id: str, pages=None):
    """Replace the chunks stored for `path` with new text, embeddings, and metadata."""
    old_ids = get_all(collection, where={"path": path}, include=[])["ids"]
    base_meta = _base_metadata_user(user_id, path, source_file)
    new_ids: list[str] = []
    # Write the new chunks first, then drop leftovers, so a failure never leaves the file empty.
    for i in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[i : i + BATCH_SIZE]
        embeddings = embed_texts(batch).tolist()
        ids = chunk_ids_for_path(path, len(batch), start=i)
        metadatas = []
        for j in range(len(batch)):
            m = {**base_meta, "chunk_index": i + j}
            if pages and pages[i + j] is not None:
                m["page"] = int(pages[i + j])
            metadatas.append(m)
        collection.upsert(documents=batch, embeddings=embeddings, ids=ids, metadatas=metadatas)
        new_ids.extend(ids)
    keep = set(new_ids)
    delete_ids(collection, [i for i in old_ids if i not in keep])


def refresh_metadata_for_paths(user_id: str, chroma_paths: list[str]) -> int:
    """Update metadata on existing chunks (e.g. after toggling primary authority)."""
    if not chroma_paths:
        return 0
    col = get_user_collection(user_id)
    updated = 0
    for path in chroma_paths:
        try:
            res = get_all(col, where={"path": path}, include=["metadatas"])
            ids = res["ids"]
            if not ids:
                continue
            metas = []
            for old in res.get("metadatas") or []:
                old = old or {}
                nm = _base_metadata_user(user_id, old.get("path") or path, old.get("source_file") or "")
                nm.update({k: old[k] for k in _CHUNK_KEYS if k in old})
                metas.append(nm)
            write_records(col, "update", ids, metadatas=metas)
            updated += len(ids)
        except Exception:
            logger.warning("Metadata refresh failed for %s", path, exc_info=True)
            continue
    return updated


def ingest_text_for_path(user_id: str, rel_path: str, source_file: str, text: str, page_starts=None):
    """Ingest or replace chunks for a single extracted text path (e.g. after vision OCR)."""
    if not text or not text.strip():
        return {"chunks_created": 0, "message": "Empty text."}
    collection = get_user_collection(user_id)
    chunks, pages = _chunk_text_with_pages(text, page_starts)
    _add_chunks_to_collection(collection, chunks, rel_path, source_file, user_id, pages)
    return {"chunks_created": len(chunks), "message": f"Ingested {len(chunks)} chunks."}


def chunks_relocate_path(user_id: str, old_path: str, new_path: str, source_file: str | None = None) -> int:
    """Move all chunks from old_path to new_path (same text and embeddings, updated metadata)."""
    col = get_user_collection(user_id)
    res = get_all(col, where={"path": old_path}, include=["documents", "embeddings", "metadatas"])
    docs = res.get("documents") or []
    ids = res["ids"]
    if not ids:
        return 0
    embs = res.get("embeddings")
    if embs is None or len(embs) != len(docs):
        embs = embed_texts(docs).tolist()
    else:
        embs = [list(e) for e in embs]
    sf = source_file or os.path.basename(new_path.replace("\\", "/"))
    base_meta = _base_metadata_user(user_id, new_path, sf)
    old_metas = res.get("metadatas") or []
    metadatas = []
    for k in range(len(docs)):
        m = dict(base_meta)
        om = old_metas[k] if k < len(old_metas) else {}
        if isinstance(om, dict) and om.get("page") is not None:
            m["page"] = om["page"]
        if isinstance(om, dict) and "chunk_index" in om:
            try:
                m["chunk_index"] = int(om["chunk_index"])
            except (TypeError, ValueError):
                m["chunk_index"] = k
        else:
            m["chunk_index"] = k
        metadatas.append(m)
    # Add the new copies before deleting the old ones, so a failure never loses the chunks.
    new_ids = chunk_ids_for_path(new_path, len(docs))
    write_records(col, "upsert", new_ids, documents=list(docs), embeddings=embs, metadatas=metadatas)
    keep = set(new_ids)
    delete_ids(col, [i for i in ids if i not in keep])
    return len(docs)


def chunks_relocate_files(user_id: str, file_paths: list[str], old_prefix: str, new_prefix: str) -> int:
    """Move the chunks of each file under old_prefix (paths taken from the folder tree) to new_prefix."""
    old_prefix = old_prefix.replace("\\", "/").strip("/")
    new_prefix = new_prefix.replace("\\", "/").strip("/")
    moved = 0
    for p in file_paths:
        if p == old_prefix or p.startswith(old_prefix + "/"):
            moved += chunks_relocate_path(user_id, p, new_prefix + p[len(old_prefix) :])
    return moved
