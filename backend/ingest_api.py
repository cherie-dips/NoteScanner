"""Ingest text into ChromaDB using the centralized client and per-user collection (user_{user_id}_notes)."""
import os
from sentence_transformers import SentenceTransformer
from langchain_text_splitters import RecursiveCharacterTextSplitter

from backend.chroma_store import get_user_collection

CHUNK_SIZE = 750
CHUNK_OVERLAP = 100
BATCH_SIZE = 50


def _chunk_text(text: str):
    splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    return splitter.split_text(text)


def _add_chunks_to_collection(collection, chunks: list, path: str, source_file: str, model):
    """Add chunks to the user collection with path/source_file metadata. Replaces existing chunks for this path."""
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
        metadatas = [{"path": path, "source_file": source_file} for _ in batch]
        collection.add(documents=batch, embeddings=embeddings, ids=ids, metadatas=metadatas)


def ingest_pdf_in_memory(user_id: str, pdf_bytes: bytes, filename: str, rel_path: str):
    """
    Read PDF from bytes, extract text in memory, chunk (~750 chars, ~100 overlap), and add to
    the user's ChromaDB collection. Does not write any file to disk.
    """
    from backend.extract_api import extract_text_from_pdf_bytes
    text = extract_text_from_pdf_bytes(pdf_bytes)
    if not text or not text.strip():
        return {"chunks_created": 0, "message": "No text extracted from PDF."}
    collection = get_user_collection(user_id)
    model = SentenceTransformer("all-MiniLM-L6-v2")
    chunks = _chunk_text(text)
    _add_chunks_to_collection(collection, chunks, rel_path, filename, model)
    return {"chunks_created": len(chunks), "message": f"Ingested {len(chunks)} chunks from PDF."}


def ingest_folder(notes_root: str, user_id: str, subject: str):
    """Ingest all text files from notes_root/subject into the user's ChromaDB collection."""
    folder_path = os.path.join(notes_root, subject)
    if not os.path.exists(folder_path):
        return {"error": "Folder does not exist.", "chunks_created": 0}

    # Collect (path, text) for each .txt file; path is relative to notes_root
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
        except Exception as e:
            print(f"❌ Error reading {fname}: {str(e)}")
            continue

    if not items:
        return {"error": "No extracted text found in folder.", "chunks_created": 0}

    collection = get_user_collection(user_id)
    model = SentenceTransformer("all-MiniLM-L6-v2")
    total_chunks = 0
    for rel_path, source_file, text in items:
        chunks = _chunk_text(text)
        _add_chunks_to_collection(collection, chunks, rel_path, source_file, model)
        total_chunks += len(chunks)
    return {
        "message": f"Ingested {total_chunks} chunks for subject '{subject}' into ChromaDB.",
        "chunks_created": total_chunks,
        "files_processed": [x[1] for x in items],
        "final_count": collection.count(),
    }
