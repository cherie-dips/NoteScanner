"""
One-time move of note data saved under the old "hidden .txt twin" naming rule.

Older uploads stored the text of `notes.png` (search chunks + extracted text) and `notes.pdf`
(extracted text only) under a hidden `notes.txt` path. That clashed with a real `notes.txt`:
deleting or re-uploading one damaged the other. New uploads always use the real file path;
this moves old data to the real path once per user.
"""
import logging
import os
import threading

from backend.chroma_store import (
    get_user_collection,
    user_lock,
    user_document_delete_by_path,
    user_document_exists,
    user_document_rename_path,
    vfs_get_storage_version,
    vfs_set_storage_version,
)
from backend.ingest_api import chunks_relocate_path
from backend.vfs_tree import flatten_file_paths

logger = logging.getLogger(__name__)

STORAGE_VERSION = 2  # 2 = text and chunks always stored under the real file path
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".gif", ".webp")

_checked_users: set[str] = set()
_checked_lock = threading.Lock()


def _move_chunks(user_id: str, old_path: str, new_path: str) -> None:
    col = get_user_collection(user_id)
    old_ids = col.get(where={"path": old_path}, include=[]).get("ids") or []
    if not old_ids:
        return
    if col.get(where={"path": new_path}, include=[], limit=1).get("ids"):
        col.delete(ids=old_ids)  # the real path already has its own data
        return
    chunks_relocate_path(user_id, old_path, new_path)


def _move_document(user_id: str, old_path: str, new_path: str) -> None:
    if not user_document_exists(user_id, old_path):
        return
    if user_document_exists(user_id, new_path):
        user_document_delete_by_path(user_id, old_path)
    else:
        user_document_rename_path(user_id, old_path, new_path)


def migrate_legacy_twin_paths(user_id: str, tree: list) -> None:
    """Run the one-time move for this user if needed. Never raises (logs instead)."""
    if user_id in _checked_users:
        return
    real_paths = set(flatten_file_paths(tree))
    if not real_paths:
        return  # nothing stored yet; check again once the user has files
    try:
        with user_lock(user_id):
            if vfs_get_storage_version(user_id) < STORAGE_VERSION:
                # Images first: when notes.png and notes.pdf share one twin, the chunks belong to the image.
                ordered = sorted(real_paths, key=lambda p: (not p.lower().endswith(_IMAGE_EXTS), p))
                for path in ordered:
                    stem, ext = os.path.splitext(path)
                    ext = ext.lower()
                    if ext != ".pdf" and ext not in _IMAGE_EXTS:
                        continue
                    twin = stem + ".txt"
                    if twin in real_paths:
                        continue  # a real .txt file owns that name; leave its data alone
                    if ext in _IMAGE_EXTS:
                        _move_chunks(user_id, twin, path)
                    _move_document(user_id, twin, path)
                vfs_set_storage_version(user_id, STORAGE_VERSION)
    except Exception:
        logger.exception("Legacy storage migration failed for %s; will retry after restart", user_id)
    with _checked_lock:
        _checked_users.add(user_id)


def forget_user(user_id: str) -> None:
    with _checked_lock:
        _checked_users.discard(user_id)
