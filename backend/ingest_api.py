"""Ingest text into ChromaDB using the centralized client and per-user collection (user_{user_id}_notes)."""
import os
from sentence_transformers import SentenceTransformer
from langchain_text_splitters import RecursiveCharacterTextSplitter

from backend.chroma_store import get_user_collection
from backend.file_meta import course_id_from_path, merged_meta_for_chunk

CHUNK_SIZE = 750
CHUNK_OVERLAP = 100
BATCH_SIZE = 50


def _chunk_text(text: str):
    splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    return splitter.split_text(text)


def _base_metadata_user(user_id: str, path: str, source_file: str) -> dict:
    fm = merged_meta_for_chunk(user_id, path, source_file)
    # Chroma metadata: str, int, float, bool — use int flags for broad server/Cloud compatibility.
    return {
        "user_id": str(user_id),
        "path": str(path or ""),
        "source_file": str(source_file or ""),
        "course_id": str(course_id_from_path(path)),
        "is_primary_authority": int(bool(fm.get("is_primary_authority"))),
        "doc_type": str(fm.get("doc_type") or "other"),
    }


def _add_chunks_to_collection(collection, chunks: list, path: str, source_file: str, model, user_id: str):
    """Add chunk text, embeddings, and metadata to the user's Chroma collection."""
    try:
        existing = collection.get(where={"path": path}, include=[])
        if existing["ids"]:
            collection.delete(ids=existing["ids"])
    except Exception:
        pass
    path_safe = path.replace("|", "\x00")
    for i in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[i : i + BATCH_SIZE]
        embeddings = model.encode(batch).tolist()
        ids = [f"{path_safe}|{i + j}" for j in range(len(batch))]
        metadatas = []
        for j, _chunk in enumerate(batch):
            m = _base_metadata_user(user_id, path, source_file)
            m["chunk_index"] = i + j
            metadatas.append(m)
        collection.add(documents=batch, embeddings=embeddings, ids=ids, metadatas=metadatas)


def refresh_metadata_for_paths(user_id: str, chroma_paths: list[str]) -> int:
    """Update metadata on existing chunks (e.g. after toggling primary authority)."""
    if not chroma_paths:
        return 0
    col = get_user_collection(user_id)
    updated = 0
    for path in chroma_paths:
        try:
            res = col.get(where={"path": path}, include=["metadatas"])
            ids = res.get("ids") or []
            if not ids:
                continue
            metas = []
            for old in res.get("metadatas") or []:
                old = old or {}
                p = old.get("path") or path
                sf = old.get("source_file") or ""
                nm = _base_metadata_user(user_id, p, sf)
                if "chunk_index" in old:
                    nm["chunk_index"] = old["chunk_index"]
                metas.append(nm)
            col.update(ids=ids, metadatas=metas)
            updated += len(ids)
        except Exception:
            continue
    return updated


def ingest_pdf_in_memory(user_id: str, pdf_bytes: bytes, filename: str, rel_path: str, notes_root: str | None = None):
    """
    Read PDF from bytes, extract text in memory, chunk (~750 chars, ~100 overlap), and add to
    the user's ChromaDB collection. Does not write any file to disk.
    """
    from backend.extract_api import extract_text_from_pdf_bytes

    _ = notes_root  # unused; kept for call-site compatibility
    text = extract_text_from_pdf_bytes(pdf_bytes)
    if not text or not text.strip():
        return {"chunks_created": 0, "message": "No text extracted from PDF."}
    collection = get_user_collection(user_id)
    model = SentenceTransformer("all-MiniLM-L6-v2")
    chunks = _chunk_text(text)
    _add_chunks_to_collection(collection, chunks, rel_path, filename, model, user_id)
    return {"chunks_created": len(chunks), "message": f"Ingested {len(chunks)} chunks from PDF."}


def ingest_folder(notes_root: str, user_id: str, subject: str):
    """Ingest all text files from notes_root/subject into the user's ChromaDB collection."""
    folder_path = os.path.join(notes_root, subject)
    if not os.path.exists(folder_path):
        return {"error": "Folder does not exist.", "chunks_created": 0}

    items = []
    for fname in os.listdir(folder_path):
        if not fname.endswith(".txt"):
            continue
        txt_path = os.path.join(folder_path, fname)
        try:
            with open(txt_path, "r", encoding="utf-8") as f:
                text = f.read()
                if text.strip():
                    rel_path = os.path.join(subject, fname).replace("\\", "/")
                    items.append((rel_path, fname, text))
        except OSError as e:
            print(f"❌ Error reading {fname}: {str(e)}")
            continue

    if not items:
        return {"error": "No extracted text found in folder.", "chunks_created": 0}

    collection = get_user_collection(user_id)
    model = SentenceTransformer("all-MiniLM-L6-v2")
    total_chunks = 0
    for rel_path, source_file, text in items:
        chunks = _chunk_text(text)
        _add_chunks_to_collection(collection, chunks, rel_path, source_file, model, user_id)
        total_chunks += len(chunks)
    return {
        "message": f"Ingested {total_chunks} chunks for subject '{subject}' into ChromaDB.",
        "chunks_created": total_chunks,
        "files_processed": [x[1] for x in items],
        "final_count": collection.count(),
    }


def ingest_text_for_path(user_id: str, rel_path: str, source_file: str, text: str):
    """Ingest or replace chunks for a single extracted text path (e.g. after vision OCR)."""
    if not text or not text.strip():
        return {"chunks_created": 0, "message": "Empty text."}
    collection = get_user_collection(user_id)
    model = SentenceTransformer("all-MiniLM-L6-v2")
    chunks = _chunk_text(text)
    _add_chunks_to_collection(collection, chunks, rel_path, source_file, model, user_id)
    return {"chunks_created": len(chunks), "message": f"Ingested {len(chunks)} chunks."}


def chunks_relocate_path(user_id: str, old_path: str, new_path: str, source_file: str | None = None) -> int:
    """Move all chunks from old_path to new_path (same text and embeddings, updated metadata)."""
    col = get_user_collection(user_id)
    res = col.get(where={"path": old_path}, include=["documents", "embeddings", "metadatas"])
    docs = res.get("documents") or []
    ids = res.get("ids") or []
    if not ids:
        return 0
    embs = res.get("embeddings")
    model = SentenceTransformer("all-MiniLM-L6-v2")
    if embs is None or len(embs) != len(docs):
        embs = model.encode(docs).tolist()
    else:
        embs = list(embs)
    col.delete(ids=ids)
    sf = source_file or os.path.basename(new_path.replace("\\", "/"))
    old_metas = res.get("metadatas") or []
    metadatas = []
    for k in range(len(docs)):
        m = _base_metadata_user(user_id, new_path, sf)
        om = old_metas[k] if k < len(old_metas) else {}
        if isinstance(om, dict) and "chunk_index" in om:
            try:
                m["chunk_index"] = int(om["chunk_index"])
            except (TypeError, ValueError):
                m["chunk_index"] = k
        else:
            m["chunk_index"] = k
        metadatas.append(m)
    path_safe = new_path.replace("|", "\x00")
    new_ids = [f"{path_safe}|{j}" for j in range(len(docs))]
    col.add(ids=new_ids, documents=list(docs), embeddings=list(embs), metadatas=metadatas)
    return len(docs)


def chunks_relocate_folder_prefix(user_id: str, old_prefix: str, new_prefix: str) -> int:
    """Rewrite chunk paths for every document under old_prefix to new_prefix."""
    old_prefix = old_prefix.replace("\\", "/").strip("/")
    new_prefix = new_prefix.replace("\\", "/").strip("/")
    col = get_user_collection(user_id)
    res = col.get(include=["metadatas"])
    metas = res.get("metadatas") or []
    paths = {((m or {}).get("path") or "").replace("\\", "/") for m in metas if (m or {}).get("path")}
    todo: list[tuple[str, str]] = []
    for p in paths:
        if p == old_prefix:
            todo.append((p, new_prefix))
        elif p.startswith(old_prefix + "/"):
            todo.append((p, new_prefix + p[len(old_prefix) :]))
    todo.sort(key=lambda x: -len(x[0]))
    moved = 0
    for old_p, new_p in todo:
        if old_p == new_p:
            continue
        sf = None
        for m in metas:
            if ((m or {}).get("path") or "").replace("\\", "/") == old_p:
                sf = (m or {}).get("source_file")
                break
        moved += chunks_relocate_path(user_id, old_p, new_p, sf)
    return moved
