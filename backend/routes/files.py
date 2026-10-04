"""Folder tree, uploads, moves/renames/deletes, viewing and editing a file's text, file metadata."""
import logging
import os

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse

from backend import legacy_migration, rate_limit, uploads, usage
from backend.chroma_store import (
    delete_user_notes_by_paths,
    delete_user_notes_collection,
    delete_user_vfs_collection,
    user_document_delete_by_path,
    user_document_delete_many,
    user_document_get,
    user_document_rename_path,
    user_document_upsert,
    user_documents_relocate_prefix,
    user_lock,
    user_notes_concat_text_for_path,
    vfs_set_tree,
)
from backend.deps import EffectiveUser, SignedInUser
from backend.file_meta import (
    delete_key,
    delete_keys_for_prefix,
    file_meta_relocate_prefix,
    file_meta_rename_path,
    load_file_meta,
    set_entry,
)
from backend.ingest_api import (
    chunks_relocate_files,
    chunks_relocate_path,
    ingest_text_for_path,
    refresh_metadata_for_paths,
)
from backend.paths import (
    file_paths,
    get_vfs_tree_clean,
    join_path,
    parent_of,
    path_exists_in_vfs,
    strip_storage_path,
    valid_name,
    vfs_entry_kind,
)
from backend.settings import (
    LIMIT_DELETE_ALL_PER_USER,
    LIMIT_TEXT_EDITS_PER_USER,
    LIMIT_TREE_CHANGES_PER_USER,
    LIMIT_UPLOADS_PER_DAY,
    LIMIT_UPLOADS_PER_USER,
    MAX_EDITED_TEXT_CHARS,
)
from backend.vfs_tree import tree_add_file, tree_add_folder, tree_has_folder, tree_move_path, tree_remove_path

logger = logging.getLogger(__name__)
router = APIRouter()


def _error(message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status_code)


def _under(prefix: str):
    return lambda p: (p or "").replace("\\", "/") == prefix or (p or "").replace("\\", "/").startswith(prefix + "/")


@router.get("/list_tree")
def list_tree(effective: EffectiveUser = None):
    user_id, _ = effective
    return {"tree": get_vfs_tree_clean(user_id)}


@router.post("/create_folder")
def create_folder(path: str = Form(""), name: str = Form(...), user_id: SignedInUser = None):
    rate_limit.check("tree-changes", user_id, *LIMIT_TREE_CHANGES_PER_USER)
    path = strip_storage_path(path)
    name = (name or "").strip().replace("\\", "/").strip("/")
    if not valid_name(name):
        return _error("Invalid folder name.")
    with user_lock(user_id):
        tree = get_vfs_tree_clean(user_id)
        if path and not tree_has_folder(tree, path):
            return _error(f"Folder '{path}' does not exist.")
        vfs_set_tree(user_id, tree_add_folder(tree, path, name))
    return JSONResponse({"message": f"Folder '{name}' created.", "path": join_path(path, name)})


@router.post("/create_file")
def create_file(path: str = Form(""), name: str = Form(...), user_id: SignedInUser = None):
    rate_limit.check("tree-changes", user_id, *LIMIT_TREE_CHANGES_PER_USER)
    path = strip_storage_path(path)
    if path and ".." in path:
        return _error("Invalid path.")
    name = (name or "").strip()
    filename = name if "." in name else f"{name}.txt"
    if not name or not valid_name(filename):
        return _error("Invalid file name.")
    rel_path = join_path(path, filename)
    with user_lock(user_id):
        tree = get_vfs_tree_clean(user_id)
        if path and not tree_has_folder(tree, path):
            return _error(f"Folder '{path}' does not exist.")
        if path_exists_in_vfs(tree, rel_path):
            return _error(f"'{filename}' already exists here.")
        vfs_set_tree(user_id, tree_add_file(tree, path, filename))
    user_document_upsert(user_id, rel_path, "", filename)
    return JSONResponse({"message": f"File '{filename}' created.", "path": rel_path})


@router.post("/upload_note")
def upload_note(path: str = Form(""), file: UploadFile = File(...), user_id: SignedInUser = None):
    """Check and queue an upload; the text is read in the background (see GET /jobs/{job_id})."""
    rate_limit.per_user(
        user_id,
        ("upload", LIMIT_UPLOADS_PER_USER, None),
        ("upload-day", LIMIT_UPLOADS_PER_DAY, "uploads"),
    )
    path = strip_storage_path(path)
    if path and ".." in path:
        return _error("Invalid path.")
    tree = get_vfs_tree_clean(user_id)
    if path and not tree_has_folder(tree, path):
        return _error(f"Folder '{path}' does not exist.")
    raw_name = uploads.clean_upload_name(file)
    uploads.check_supported_type(raw_name)
    usage.check_budget()
    data = uploads.read_upload(file)
    job = uploads.submit_upload_job(user_id, path, raw_name, data)
    usage.record(user_id, uploads=1)
    return JSONResponse(job, status_code=202)


@router.get("/jobs")
def list_jobs(user_id: SignedInUser = None):
    return {"jobs": uploads.list_jobs(user_id)}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, user_id: SignedInUser = None):
    job = uploads.get_job(user_id, job_id)
    if not job:
        return _error("Upload not found (it may have finished more than an hour ago).", 404)
    return job


def _relocate_data(user_id: str, tree: list, kind: str, src: str, dest: str) -> None:
    """Move search chunks, stored text and metadata from src to dest (file or whole folder)."""
    if kind == "file":
        chunks_relocate_path(user_id, src, dest)
        user_document_rename_path(user_id, src, dest)
        file_meta_rename_path(user_id, src, dest)
    else:
        chunks_relocate_files(user_id, file_paths(tree), src, dest)
        user_documents_relocate_prefix(user_id, src, dest)
        file_meta_relocate_prefix(user_id, src, dest)


@router.post("/delete_path")
def delete_path(path: str = Form(...), kind: str = Form(...), user_id: SignedInUser = None):
    """Remove a file or folder from the virtual tree and Chroma (no server-side binaries)."""
    rate_limit.check("tree-changes", user_id, *LIMIT_TREE_CHANGES_PER_USER)
    path = strip_storage_path(path)
    if not path or ".." in path:
        return _error("Invalid path.")
    if kind not in ("file", "folder"):
        return _error("Invalid kind. Use 'file' or 'folder'.")
    with user_lock(user_id):
        tree = get_vfs_tree_clean(user_id)
        if vfs_entry_kind(tree, path) != kind:
            return _error(f"Not a {kind} or not found.")
        # Data is removed before the tree entry: if a step fails, the item stays visible and can be retried.
        if kind == "file":
            delete_user_notes_by_paths(user_id, [path])
            user_document_delete_by_path(user_id, path)
            delete_key(user_id, path)
        else:
            delete_user_notes_by_paths(user_id, [p for p in file_paths(tree) if _under(path)(p)])
            user_document_delete_many(user_id, path_filter=_under(path))
            delete_keys_for_prefix(user_id, path)
        vfs_set_tree(user_id, tree_remove_path(tree, path))
    return JSONResponse({"message": "File deleted." if kind == "file" else "Folder deleted."})


@router.post("/move_path")
def move_path(from_path: str = Form(...), to_folder: str = Form(""), user_id: SignedInUser = None):
    """Move a file or folder in the virtual tree and update Chroma paths."""
    rate_limit.check("tree-changes", user_id, *LIMIT_TREE_CHANGES_PER_USER)
    from_path = strip_storage_path(from_path)
    to_folder = strip_storage_path(to_folder)
    if ".." in from_path or ".." in to_folder:
        return _error("Invalid path.")
    with user_lock(user_id):
        tree = get_vfs_tree_clean(user_id)
        kind = vfs_entry_kind(tree, from_path)
        if not kind:
            return _error("Path not found.")
        if kind == "folder" and (to_folder == from_path or to_folder.startswith(from_path + "/")):
            return _error("A folder can't be moved into itself.")
        name = os.path.basename(from_path)
        dest_rel = join_path(to_folder, name)
        if dest_rel == from_path:
            return JSONResponse({"message": "Already there.", "path": dest_rel})
        if path_exists_in_vfs(tree, dest_rel):
            return _error(f"'{name}' already exists in destination.")
        # Move the data first, then the tree entry, so the tree never points at data that isn't there.
        _relocate_data(user_id, tree, kind, from_path, dest_rel)
        vfs_set_tree(user_id, tree_move_path(tree, from_path, to_folder))
    return JSONResponse({"message": "Moved.", "path": dest_rel})


@router.post("/rename_path")
def rename_path(path: str = Form(...), new_name: str = Form(...), user_id: SignedInUser = None):
    rate_limit.check("tree-changes", user_id, *LIMIT_TREE_CHANGES_PER_USER)
    path = strip_storage_path(path)
    new_name = (new_name or "").strip()
    if not path or ".." in path:
        return _error("Invalid path.")
    if not valid_name(new_name):
        return _error("Names can't be empty or contain '/' or '..'.")
    with user_lock(user_id):
        tree = get_vfs_tree_clean(user_id)
        kind = vfs_entry_kind(tree, path)
        if not kind:
            return _error("Path not found.")
        parent = parent_of(path)
        dest = join_path(parent, new_name)
        if dest == path:
            return JSONResponse({"message": "Name unchanged.", "path": dest, "kind": kind})
        if path_exists_in_vfs(tree, dest):
            return _error(f"'{new_name}' already exists here.")
        _relocate_data(user_id, tree, kind, path, dest)
        vfs_set_tree(user_id, tree_move_path(tree, path, parent, new_name=new_name))
    return JSONResponse({"message": "Renamed.", "path": dest, "kind": kind})


@router.get("/file_text")
def get_file_text(path: str = "", user_id: SignedInUser = None):
    """The text NoteScanner has for a file (what search and study tools use)."""
    p = strip_storage_path(path)
    if vfs_entry_kind(get_vfs_tree_clean(user_id), p) != "file":
        return _error("File not found.", 404)
    doc = user_document_get(user_id, p)
    if doc and not doc["truncated"]:
        return {"path": p, "text": doc["content"], "truncated": False}
    # Older uploads only kept the first part of the text; rebuild the rest from the search chunks.
    chunk_text = user_notes_concat_text_for_path(user_id, p)
    if chunk_text:
        return {"path": p, "text": chunk_text, "truncated": False}
    return {"path": p, "text": doc["content"] if doc else "", "truncated": bool(doc)}


@router.post("/file_text")
def save_file_text(path: str = Form(...), text: str = Form(""), user_id: SignedInUser = None):
    """Replace a file's text (e.g. to fix reading mistakes, or to write notes in an empty file)."""
    rate_limit.check("text-edits", user_id, *LIMIT_TEXT_EDITS_PER_USER)
    p = strip_storage_path(path)
    if len(text) > MAX_EDITED_TEXT_CHARS:
        return _error(f"Text is too long (maximum {MAX_EDITED_TEXT_CHARS:,} characters).")
    tree = get_vfs_tree_clean(user_id)
    if vfs_entry_kind(tree, p) != "file":
        return _error("File not found.", 404)
    name = os.path.basename(p)
    if text.strip():
        chunks = ingest_text_for_path(user_id, p, name, text)["chunks_created"]
    else:
        delete_user_notes_by_paths(user_id, [p])
        chunks = 0
    user_document_upsert(user_id, p, text, name)
    return JSONResponse({"message": "Saved.", "chunks": chunks})


@router.get("/file_meta")
def get_file_meta_route(effective: EffectiveUser = None):
    user_id, _ = effective
    return {"meta": load_file_meta(user_id)}


@router.post("/file_meta")
def post_file_meta(
    path: str = Form(...),
    is_primary_authority: str = Form("false"),
    doc_type: str = Form(""),
    related_paths: str = Form(""),
    user_id: SignedInUser = None,
):
    rate_limit.check("tree-changes", user_id, *LIMIT_TREE_CHANGES_PER_USER)
    path = strip_storage_path(path)
    primary = str(is_primary_authority).lower() in ("1", "true", "yes", "on")
    rel_list = [strip_storage_path(x.strip()) for x in related_paths.split(",") if x.strip()]
    entry = set_entry(
        user_id,
        path,
        is_primary_authority=primary,
        doc_type=(doc_type or "other"),
        related_paths=rel_list,
    )
    n = refresh_metadata_for_paths(user_id, [path])
    return JSONResponse({"path": path, "entry": entry, "chunks_updated": n})


@router.delete("/notes")
def delete_notes(file: str | None = None, files: str | None = None, user_id: SignedInUser = None):
    """
    Delete the user's notes.
    - No query params: delete all notes, folders and stored text.
    - file=<path> / files=<path1>,<path2>: delete only the search chunks for those paths.
    """
    if file or files:
        paths = [p.strip() for p in ([file] if file else files.split(",")) if p and p.strip()]
        if not paths:
            return _error("No paths provided.")
        if any(".." in p or p.startswith("/") for p in paths):
            return _error("Invalid path.")
        deleted = delete_user_notes_by_paths(user_id, [strip_storage_path(p) for p in paths])
        return JSONResponse({"message": f"Deleted {deleted} chunk(s) for the given file(s).", "deleted_count": deleted})
    rate_limit.check("delete-all", user_id, *LIMIT_DELETE_ALL_PER_USER)
    with user_lock(user_id):
        deleted_notes = delete_user_notes_collection(user_id)
        deleted_vfs = delete_user_vfs_collection(user_id)
        user_document_delete_many(user_id, path_filter=None)
        legacy_migration.forget_user(user_id)
    return JSONResponse({
        "message": "All your notes have been deleted.",
        "deleted_notes": deleted_notes,
        "deleted_vfs": deleted_vfs,
    })
