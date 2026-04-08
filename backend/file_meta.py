"""Per-user file metadata (primary authority, related paths) stored in ChromaDB (vfs collection)."""
from typing import Any

from backend.chroma_store import vfs_get_file_meta_dict, vfs_set_file_meta_dict


def load_file_meta(user_id: str) -> dict[str, dict[str, Any]]:
    return vfs_get_file_meta_dict(user_id)


def save_file_meta(user_id: str, data: dict[str, dict[str, Any]]) -> None:
    vfs_set_file_meta_dict(user_id, data)


def get_entry(user_id: str, rel_path: str) -> dict[str, Any]:
    rel_path = rel_path.replace("\\", "/").strip("/")
    meta = load_file_meta(user_id)
    return dict(meta.get(rel_path) or {})


def set_entry(user_id: str, rel_path: str, **fields) -> dict[str, Any]:
    rel_path = rel_path.replace("\\", "/").strip("/")
    meta = load_file_meta(user_id)
    cur = dict(meta.get(rel_path) or {})
    for k, v in fields.items():
        if v is None:
            cur.pop(k, None)
        else:
            cur[k] = v
    meta[rel_path] = cur
    save_file_meta(user_id, meta)
    return cur


def delete_keys_for_prefix(user_id: str, folder_prefix: str) -> None:
    folder_prefix = folder_prefix.replace("\\", "/").strip("/")
    meta = load_file_meta(user_id)
    keys = [k for k in meta if k == folder_prefix or k.startswith(folder_prefix + "/")]
    for k in keys:
        del meta[k]
    save_file_meta(user_id, meta)


def delete_key(user_id: str, rel_path: str) -> None:
    rel_path = rel_path.replace("\\", "/").strip("/")
    meta = load_file_meta(user_id)
    if rel_path in meta:
        del meta[rel_path]
        save_file_meta(user_id, meta)


def chunk_paths_for_file(rel_path: str) -> list[str]:
    """Chroma `path` values to update when user toggles metadata on this file."""
    import os

    rel_path = rel_path.replace("\\", "/").strip("/")
    lower = rel_path.lower()
    out = [rel_path]
    if lower.endswith((".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".gif", ".webp")):
        base = os.path.splitext(rel_path)[0] + ".txt"
        if base != rel_path:
            out.append(base)
    return list(dict.fromkeys(out))


def merged_meta_for_chunk(
    user_id: str,
    chunk_path: str,
    source_file: str,
) -> dict[str, Any]:
    """Resolve file_meta for a chunk: try chunk path, .pdf/.txt sibling, and image→txt."""
    import os

    chunk_path = chunk_path.replace("\\", "/")
    meta = load_file_meta(user_id)
    candidates = [chunk_path]
    base, ext = os.path.splitext(chunk_path)
    ext_l = ext.lower()
    if ext_l == ".txt":
        for img_ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"):
            candidates.append(base + img_ext)
        if base:
            candidates.append(base + ".pdf")
    elif ext_l == ".pdf":
        candidates.append(base + ".txt")
    for c in candidates:
        c = c.strip("/")
        if c in meta:
            return dict(meta[c])
    return {}


def file_meta_rename_path(user_id: str, old_p: str, new_p: str) -> None:
    old_p = old_p.replace("\\", "/").strip("/")
    new_p = new_p.replace("\\", "/").strip("/")
    meta = load_file_meta(user_id)
    if old_p not in meta:
        return
    meta[new_p] = meta.pop(old_p)
    save_file_meta(user_id, meta)


def file_meta_relocate_prefix(user_id: str, old_prefix: str, new_prefix: str) -> None:
    old_prefix = old_prefix.replace("\\", "/").strip("/")
    new_prefix = new_prefix.replace("\\", "/").strip("/")
    meta = load_file_meta(user_id)
    keys = sorted(
        [k for k in meta if k == old_prefix or k.startswith(old_prefix + "/")],
        key=lambda x: -len(x),
    )
    for k in keys:
        if k == old_prefix:
            nk = new_prefix
        else:
            nk = new_prefix + "/" + k[len(old_prefix) + 1 :]
        meta[nk] = meta.pop(k)
    save_file_meta(user_id, meta)


def course_id_from_path(rel_path: str) -> str:
    rel_path = rel_path.replace("\\", "/").strip("/")
    if not rel_path:
        return "root"
    return rel_path.split("/")[0]
