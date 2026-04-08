"""Virtual folder tree mutations (explorer). Stored in ChromaDB, not on disk."""
from __future__ import annotations

import copy
from typing import Any


def _norm(p: str) -> str:
    return (p or "").replace("\\", "/").strip("/")


def tree_has_folder(tree: list, folder_path: str) -> bool:
    """True if folder_path exists as a folder in the tree (empty path = root)."""
    fp = _norm(folder_path)
    if not fp:
        return True
    parts = [x for x in fp.split("/") if x]
    cur = tree
    for part in parts:
        found = None
        for n in cur:
            if n.get("type") == "folder" and n.get("name") == part:
                found = n
                break
        if not found:
            return False
        cur = found.get("children") or []
    return True


def join_rel(parent: str, name: str) -> str:
    p = _norm(parent)
    return f"{p}/{name}" if p else name


def ensure_folder_chain(tree: list, parent_path: str) -> list:
    """Return the children list under parent_path (create folders as needed)."""
    if not _norm(parent_path):
        return tree
    parts = [x for x in _norm(parent_path).split("/") if x]
    cur = tree
    acc: list[str] = []
    for part in parts:
        acc.append(part)
        full = "/".join(acc)
        found = None
        for node in cur:
            if node.get("type") == "folder" and node.get("name") == part:
                found = node
                break
        if not found:
            new_f: dict[str, Any] = {"type": "folder", "name": part, "path": full, "children": []}
            cur.append(new_f)
            found = new_f
        cur = found.setdefault("children", [])
    return cur


def tree_add_folder(tree: list, parent_path: str, name: str) -> list:
    t = copy.deepcopy(tree)
    children = ensure_folder_chain(t, parent_path)
    full = join_rel(parent_path, name)
    if any(n.get("path") == full for n in children):
        return t
    children.append({"type": "folder", "name": name, "path": full, "children": []})
    return t


def tree_add_file(tree: list, parent_path: str, filename: str) -> list:
    t = copy.deepcopy(tree)
    children = ensure_folder_chain(t, parent_path)
    full = join_rel(parent_path, filename)
    full = full.replace("\\", "/")
    if any(n.get("path") == full for n in children):
        return t
    children.append({"type": "file", "name": filename, "path": full})
    return t


def tree_remove_path(tree: list, target_path: str) -> list:
    """Remove a file, or a folder and everything under it."""
    tp = _norm(target_path)

    def rec(nodes: list) -> list:
        out: list = []
        for n in nodes:
            p = _norm(n.get("path") or "")
            if n.get("type") == "folder":
                if p == tp or p.startswith(tp + "/"):
                    continue
                out.append({**n, "children": rec(n.get("children") or [])})
            else:
                if p == tp or p.startswith(tp + "/"):
                    continue
                out.append(n)
        return out

    return rec(copy.deepcopy(tree))


def tree_move_path(tree: list, from_path: str, to_folder: str, new_name: str | None = None) -> list:
    """
    Move a file or folder subtree. to_folder is destination directory ("" = root).
    new_name optional rename (basename of root of moved subtree).
    """
    fp = _norm(from_path)
    dest_parent = _norm(to_folder)
    removed: dict | None = None

    def extract(nodes: list) -> list:
        nonlocal removed
        out = []
        for n in nodes:
            p = _norm(n.get("path") or "")
            if n.get("type") == "folder":
                if p == fp:
                    removed = copy.deepcopy(n)
                    continue
                out.append({**n, "children": extract(n.get("children") or [])})
            else:
                if p == fp:
                    removed = copy.deepcopy(n)
                    continue
                out.append(n)
        return out

    t = extract(copy.deepcopy(tree))
    if not removed:
        return copy.deepcopy(tree)

    old_root = _norm(removed["path"])
    nm = new_name or removed["name"]
    new_root = join_rel(dest_parent, nm)

    def rebase(n: dict) -> dict:
        n = copy.deepcopy(n)
        p = _norm(n["path"])
        if p == old_root:
            n["path"] = new_root
        elif p.startswith(old_root + "/"):
            n["path"] = new_root + "/" + p[len(old_root) + 1 :]
        if n.get("type") == "folder":
            n["children"] = [rebase(c) for c in n.get("children") or []]
        return n

    rebased = rebase(removed)
    children = ensure_folder_chain(t, dest_parent)
    children.append(rebased)
    return t


def flatten_file_paths(tree: list) -> list[str]:
    out: list[str] = []

    def walk(nodes: list) -> None:
        for n in nodes:
            if n.get("type") == "folder":
                walk(n.get("children") or [])
            else:
                p = (n.get("path") or "").replace("\\", "/")
                if p:
                    out.append(p)

    walk(tree)
    return out
