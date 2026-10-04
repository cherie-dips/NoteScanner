"""Virtual-path cleanup and folder-tree reads shared by the routes."""
import copy

from backend.chroma_store import user_lock, vfs_get_tree, vfs_set_tree
from backend.legacy_migration import migrate_legacy_twin_paths
from backend.vfs_tree import flatten_file_paths


def normalize_relative_path(path: str) -> str:
    """Use forward slashes and strip leading slashes for cross-platform consistency."""
    if not path or not path.strip():
        return ""
    return path.replace("\\", "/").strip("/")


def _is_likely_user_id_segment(seg: str) -> bool:
    if not seg:
        return False
    if seg.startswith("guest_"):
        return True
    if len(seg) == 36 and seg.count("-") == 4:
        return True
    return False


def strip_storage_path(p: str) -> str:
    """
    Remove legacy server-side prefixes from virtual paths so course folders stay at the root
    (e.g. user_notes/<id>/Course/a.pdf -> Course/a.pdf). Never persist user_notes in vfs.
    """
    p = normalize_relative_path(p)
    if not p:
        return ""
    parts = [x for x in p.split("/") if x and x != "."]
    while parts and parts[0].lower() == "user_notes":
        parts.pop(0)
    if parts and _is_likely_user_id_segment(parts[0]):
        parts.pop(0)
    return "/".join(parts)


def valid_name(name: str) -> bool:
    """A single file or folder name: not empty, no slashes, not '.' or '..'."""
    return bool(name) and "/" not in name and "\\" not in name and ".." not in name and name not in (".",)


def parent_of(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def join_path(parent: str, name: str) -> str:
    return f"{parent}/{name}" if parent else name


def _normalize_vfs_tree(tree: list) -> list:
    """Deep copy of explorer tree with every path passed through strip_storage_path."""
    out = copy.deepcopy(tree or [])

    def rec(nodes: list) -> None:
        for n in nodes:
            if "path" in n:
                n["path"] = strip_storage_path(str(n.get("path") or ""))
            if n.get("type") == "folder":
                rec(n.get("children") or [])

    rec(out)
    return out


def get_vfs_tree_clean(user_id: str) -> list:
    """Return vfs tree with no user_notes/legacy id prefixes; persist if corrected."""
    raw = vfs_get_tree(user_id)
    fixed = _normalize_vfs_tree(raw)
    if fixed != raw:
        with user_lock(user_id):
            fixed = _normalize_vfs_tree(vfs_get_tree(user_id))
            vfs_set_tree(user_id, fixed)
    migrate_legacy_twin_paths(user_id, fixed)
    return fixed


def file_paths(tree: list) -> list[str]:
    return [p.replace("\\", "/") for p in flatten_file_paths(tree)]


def _folder_paths(tree: list) -> set[str]:
    out: set[str] = set()

    def rec(nodes: list) -> None:
        for n in nodes:
            if n.get("type") == "folder":
                out.add(normalize_relative_path(n.get("path") or ""))
                rec(n.get("children") or [])

    rec(tree)
    return out


def vfs_entry_kind(tree: list, target_path: str) -> str | None:
    """Return 'file', 'folder', or None if path is not in the virtual tree."""
    tp = strip_storage_path(target_path)
    if not tp:
        return None
    if tp in set(file_paths(tree)):
        return "file"
    return "folder" if tp in _folder_paths(tree) else None


def path_exists_in_vfs(tree: list, rel: str) -> bool:
    return vfs_entry_kind(tree, rel) is not None
