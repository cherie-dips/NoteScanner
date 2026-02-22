from fastapi import FastAPI, UploadFile, File, Form, Depends, Request, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import os
import re
import shutil
import uuid
from sentence_transformers import SentenceTransformer
from langchain_groq import ChatGroq
from dotenv import load_dotenv
from typing import Annotated

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
)
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

NOTES_BASE = os.getenv("NOTES_BASE", "user_notes")
os.makedirs(NOTES_BASE, exist_ok=True)


def _user_notes_root(user_id: str) -> str:
    path = os.path.join(NOTES_BASE, user_id)
    os.makedirs(path, exist_ok=True)
    return path


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


# ---------- Delete file or folder ----------
def _normalize_relative_path(path: str) -> str:
    """Use forward slashes and strip leading slashes for cross-platform consistency."""
    if not path or not path.strip():
        return ""
    return path.replace("\\", "/").strip("/")


@app.post("/delete_path")
def delete_path(
    path: str = Form(...),
    kind: str = Form(...),  # "file" | "folder"
    effective: Annotated[tuple[str, bool], Depends(get_effective_user_dep)] = None,
):
    """Delete a file or folder and remove related DB/Chroma data."""
    user_id, is_guest = effective
    path = _normalize_relative_path(path)
    if ".." in path:
        return JSONResponse({"error": "Invalid path."}, status_code=400)
    root = _user_notes_root(user_id)
    root_abs = os.path.abspath(root)
    full_path = os.path.normpath(os.path.join(root, path)) if path else root
    full_abs = os.path.abspath(full_path)
    if full_abs != root_abs and not full_abs.startswith(root_abs + os.sep):
        return JSONResponse({"error": "Invalid path."}, status_code=400)
    if kind == "file":
        if not os.path.isfile(full_path):
            return JSONResponse({"error": "Not a file or not found."}, status_code=400)
        try:
            os.remove(full_path)
        except OSError as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        base, ext = os.path.splitext(full_path)
        txt_path = base + ".txt"
        if os.path.isfile(txt_path):
            try:
                os.remove(txt_path)
            except OSError:
                pass
        if not is_guest:
            user_document_delete_by_path(user_id, path)
            txt_path = path.replace(os.path.splitext(path)[1], ".txt")
            user_document_delete_by_path(user_id, txt_path)
            try:
                col = get_user_collection(user_id)
                ids_to_drop = []
                for p in (path, path.replace(os.path.splitext(path)[1], ".txt")):
                    res = col.get(where={"path": p}, include=[])
                    ids_to_drop.extend(res.get("ids") or [])
                if ids_to_drop:
                    col.delete(ids=ids_to_drop)
            except Exception:
                pass
        return JSONResponse({"message": "File deleted."})
    elif kind == "folder":
        if not os.path.isdir(full_path):
            return JSONResponse({"error": "Not a folder or not found."}, status_code=400)
        try:
            shutil.rmtree(full_path)
        except OSError as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        if not is_guest:
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
        return JSONResponse({"message": "Folder deleted."})
    return JSONResponse({"error": "Invalid kind. Use 'file' or 'folder'."}, status_code=400)


@app.post("/move_path")
def move_path(
    from_path: str = Form(...),
    to_folder: str = Form(""),
    effective: Annotated[tuple[str, bool], Depends(get_effective_user_dep)] = None,
):
    """Move a file or folder to another folder. to_folder is the destination folder path (empty = root)."""
    user_id, is_guest = effective
    from_path = _normalize_relative_path(from_path)
    to_folder = _normalize_relative_path(to_folder)
    if ".." in from_path or ".." in to_folder:
        return JSONResponse({"error": "Invalid path."}, status_code=400)
    root = _user_notes_root(user_id)
    root_abs = os.path.abspath(root)
    full_from = os.path.normpath(os.path.join(root, from_path))
    full_from_abs = os.path.abspath(full_from)
    if not full_from_abs.startswith(root_abs + os.sep) and full_from_abs != root_abs:
        return JSONResponse({"error": "Invalid path."}, status_code=400)
    name = os.path.basename(from_path)
    dest_rel = (os.path.join(to_folder, name).replace("\\", "/") if to_folder else name)
    full_dest = os.path.normpath(os.path.join(root, dest_rel))
    full_dest_abs = os.path.abspath(full_dest)
    if full_dest_abs == full_from_abs:
        return JSONResponse({"message": "Already there.", "path": dest_rel})
    if not full_dest_abs.startswith(root_abs + os.sep) and full_dest_abs != root_abs:
        return JSONResponse({"error": "Invalid destination."}, status_code=400)
    if os.path.exists(full_dest):
        return JSONResponse({"error": f"'{name}' already exists in destination."}, status_code=400)
    try:
        shutil.move(full_from, full_dest)
    except OSError as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    if not is_guest:
        if os.path.isfile(full_dest):
            user_document_delete_by_path(user_id, from_path)
            user_document_delete_by_path(user_id, from_path.replace(os.path.splitext(from_path)[1], ".txt"))
            try:
                col = get_user_collection(user_id)
                for p in (from_path, from_path.replace(os.path.splitext(from_path)[1], ".txt")):
                    res = col.get(where={"path": p}, include=[])
                    if res.get("ids"):
                        col.delete(ids=res["ids"])
            except Exception:
                pass
        else:
            safe_prefix = from_path.rstrip("/").replace("\\", "/")
            def path_prefix_match(p):
                np = (p or "").replace("\\", "/")
                return np == safe_prefix or np.startswith(safe_prefix + "/")
            user_document_delete_many(user_id, path_filter=path_prefix_match)
            try:
                col = get_user_collection(user_id)
                res = col.get(include=["metadatas"])
                metas = res.get("metadatas") or []
                ids_to_drop = [
                    res["ids"][i] for i, meta in enumerate(metas)
                    if path_prefix_match((meta or {}).get("path") or (meta or {}).get("source_path") or "")
                ]
                if ids_to_drop:
                    col.delete(ids=ids_to_drop)
            except Exception:
                pass
    return JSONResponse({"message": "Moved.", "path": dest_rel})


# ---------- Serve user/guest files ----------
@app.get("/user_notes/{path:path}")
def serve_user_file(path: str, request: Request):
    path = _normalize_relative_path(path)
    if ".." in path:
        return JSONResponse({"error": "Invalid path."}, status_code=400)
    session_id = request.query_params.get("session_id") or request.headers.get(SESSION_HEADER)
    guest_id = request.query_params.get("guest_id") or request.headers.get(GUEST_HEADER)
    def _serve_file(full_path: str):
        return FileResponse(
            full_path,
            media_type="application/pdf" if full_path.lower().endswith(".pdf") else None,
            headers={"Content-Disposition": "inline"},
        )
    root_abs = None
    if session_id:
        user_id = get_user_id_from_session(session_id)
        if user_id:
            root = _user_notes_root(user_id)
            root_abs = os.path.abspath(root)
            full_path = os.path.normpath(os.path.join(root, path))
            full_abs = os.path.abspath(full_path)
            if (full_abs == root_abs or full_abs.startswith(root_abs + os.sep)) and os.path.isfile(full_path):
                return _serve_file(full_path)
    if guest_id:
        effective_id = f"guest_{guest_id}"
        root = _user_notes_root(effective_id)
        root_abs = os.path.abspath(root)
        full_path = os.path.normpath(os.path.join(root, path))
        full_abs = os.path.abspath(full_path)
        if (full_abs == root_abs or full_abs.startswith(root_abs + os.sep)) and os.path.isfile(full_path):
            return _serve_file(full_path)
    return JSONResponse({"error": "Unauthorized"}, status_code=401)


# ---------- Note operations (work for both signed-in user and guest; no MongoDB save for guest) ----------
@app.post("/create_folder")
def create_folder(
    path: str = Form(""),
    name: str = Form(...),
    effective: Annotated[tuple[str, bool], Depends(get_effective_user_dep)] = None,
):
    user_id, is_guest = effective
    root = _user_notes_root(user_id)
    folder_path = os.path.join(root, path, name) if path else os.path.join(root, name)
    os.makedirs(folder_path, exist_ok=True)
    return JSONResponse({"message": f"Folder '{name}' created.", "path": folder_path})


@app.post("/create_file")
def create_file(
    path: str = Form(""),
    name: str = Form(...),
    effective: Annotated[tuple[str, bool], Depends(get_effective_user_dep)] = None,
):
    user_id, is_guest = effective
    root = _user_notes_root(user_id)
    folder_path = os.path.join(root, path) if path else root
    if not os.path.exists(folder_path):
        return JSONResponse({"error": f"Folder '{path}' does not exist."}, status_code=400)
    filename = name if "." in name else f"{name}.txt"
    file_path = os.path.join(folder_path, filename)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("")
    if not is_guest:
        rel_path = os.path.join(path, filename) if path else filename
        user_document_upsert(user_id, rel_path, "", name)
    return JSONResponse({"message": f"File '{filename}' created.", "path": file_path})


@app.post("/upload_note")
def upload_note(
    path: str = Form(""),
    file: UploadFile = File(...),
    effective: Annotated[tuple[str, bool], Depends(get_effective_user_dep)] = None,
):
    user_id, is_guest = effective
    root = _user_notes_root(user_id)
    folder_path = os.path.join(root, path) if path else root
    if not os.path.exists(folder_path):
        return JSONResponse({"error": f"Folder '{path}' does not exist."}, status_code=400)

    file_content = file.file.read()
    file_path = os.path.join(folder_path, file.filename)
    with open(file_path, "wb") as f:
        f.write(file_content)

    rel_path = (os.path.join(path, file.filename) if path else file.filename).replace("\\", "/")
    is_pdf = file.filename.lower().endswith(".pdf")

    try:
        if is_pdf and not is_guest:
            from backend.extract_api import extract_text_from_pdf_bytes
            from backend.ingest_api import ingest_pdf_in_memory
            text = extract_text_from_pdf_bytes(file_content)
            rel_path_txt = rel_path.replace(os.path.splitext(rel_path)[1], ".txt")
            user_document_upsert(user_id, rel_path_txt, text, file.filename)
            ingest_result = ingest_pdf_in_memory(user_id, file_content, file.filename, rel_path)
            return JSONResponse({
                "message": f"File '{file.filename}' uploaded and processed successfully.",
                "path": file_path,
                "ingestion": ingest_result,
            })
        if not is_pdf:
            from backend.extract_api import extract_text
            from backend.ingest_api import ingest_folder
            subject = path.split("/")[0] if "/" in path else (path or "notes")
            if not subject:
                parts = [p for p in os.path.normpath(folder_path).split(os.sep) if p and p != root]
                subject = parts[0] if parts else "notes"
            extract_result = extract_text(root, subject)
            processed = extract_result.get("processed_files") or []
            extracted_texts = extract_result.get("extracted_texts") or {}
            if not is_guest:
                for item in processed:
                    orig = item.get("original_file")
                    txt_name = item.get("text_file")
                    if not orig or not txt_name or orig not in extracted_texts:
                        continue
                    rel_path_txt = os.path.join(path, txt_name) if path else txt_name
                    user_document_upsert(user_id, rel_path_txt, extracted_texts[orig], orig)
            if processed:
                ingest_result = ingest_folder(root, user_id, subject)
                return JSONResponse({
                    "message": f"File '{file.filename}' uploaded and processed successfully.",
                    "path": file_path,
                    "extraction": extract_result,
                    "ingestion": ingest_result,
                })
        return JSONResponse({
            "message": f"File '{file.filename}' uploaded.",
            "path": file_path,
        })
    except Exception as e:
        return JSONResponse({
            "message": f"File '{file.filename}' uploaded but processing failed: {str(e)}",
            "path": file_path,
        }, status_code=500)


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
        deleted = delete_user_notes_by_paths(user_id, paths)
        return JSONResponse({"message": f"Deleted {deleted} chunk(s) for the given file(s).", "deleted_count": deleted})
    deleted = delete_user_notes_collection(user_id)
    return JSONResponse({"message": "All notes data deleted." if deleted else "No notes collection found.", "deleted": deleted})


@app.get("/list_tree")
def list_tree(effective: Annotated[tuple[str, bool], Depends(get_effective_user_dep)] = None):
    user_id, _ = effective
    root = _user_notes_root(user_id)
    if not os.path.isdir(root):
        return {"tree": []}

    def build_tree(folder):
        children = []
        for item in os.listdir(folder):
            full_path = os.path.join(folder, item)
            rel_path = os.path.relpath(full_path, root)
            if os.path.isdir(full_path):
                children.append({
                    "type": "folder",
                    "name": item,
                    "path": rel_path,
                    "children": build_tree(full_path),
                })
            else:
                children.append({"type": "file", "name": item, "path": rel_path})
        return children

    return {"tree": build_tree(root)}


@app.post("/query_folder")
def query_notes(
    subject: str = Form("notes"),
    query: str = Form(...),
    effective: Annotated[tuple[str, bool], Depends(get_effective_user_dep)] = None,
):
    user_id, _ = effective
    try:
        collection = get_user_collection(user_id)
        doc_count = collection.count()
        if doc_count == 0:
            return JSONResponse({
                "error": "No documents indexed yet. Upload and process PDFs or images first.",
                "query": query,
                "answer": "",
                "source_documents": [],
            }, status_code=404)

        model = SentenceTransformer("all-MiniLM-L6-v2")
        query_embedding = model.encode([query]).tolist()[0]
        results = collection.query(query_embeddings=[query_embedding], n_results=4)
        retrieved_docs = []
        for i, doc_text in enumerate(results["documents"][0]):
            retrieved_docs.append({
                "content": doc_text,
                "metadata": {"id": results["ids"][0][i], "distance": results["distances"][0][i]},
            })

        groq_api_key = os.getenv("GROQ_API_KEY")
        if not groq_api_key:
            return JSONResponse({
                "error": "GROQ_API_KEY not set. Add it to .env for AI answers.",
                "query": query,
                "answer": "",
                "source_documents": retrieved_docs,
            }, status_code=503)

        context = "\n\n".join([d["content"] for d in retrieved_docs])
        llm = ChatGroq(api_key=groq_api_key, model="llama-3.1-8b-instant")
        prompt = f"""Based on the following context, answer the question. If the answer cannot be found in the context, say so.

Context:
{context}

Question: {query}

Answer:"""
        response = llm.invoke(prompt)
        answer = response.content if hasattr(response, "content") else str(response)
        return JSONResponse({
            "query": query,
            "answer": answer,
            "source_documents": retrieved_docs,
        })
    except Exception as e:
        return JSONResponse({
            "error": f"Query failed: {str(e)}",
            "query": query,
            "answer": "",
            "source_documents": [],
        }, status_code=500)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
