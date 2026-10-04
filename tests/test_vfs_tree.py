from backend.vfs_tree import (
    flatten_file_paths,
    tree_add_file,
    tree_add_folder,
    tree_has_folder,
    tree_move_path,
    tree_remove_path,
)


def build():
    t = tree_add_folder([], "", "Bio")
    t = tree_add_folder(t, "Bio", "Week1")
    t = tree_add_file(t, "Bio/Week1", "cells.pdf")
    t = tree_add_file(t, "", "todo.txt")
    return t


def test_add_and_flatten():
    t = build()
    assert set(flatten_file_paths(t)) == {"Bio/Week1/cells.pdf", "todo.txt"}
    assert tree_has_folder(t, "Bio/Week1")
    assert not tree_has_folder(t, "Chem")


def test_add_is_idempotent():
    t = build()
    assert tree_add_file(t, "Bio/Week1", "cells.pdf") == t


def test_remove_folder_removes_children():
    t = tree_remove_path(build(), "Bio")
    assert flatten_file_paths(t) == ["todo.txt"]


def test_move_folder_rebases_paths():
    t = tree_add_folder(build(), "", "Archive")
    t = tree_move_path(t, "Bio", "Archive")
    assert "Archive/Bio/Week1/cells.pdf" in flatten_file_paths(t)


def test_rename_updates_name_and_paths():
    t = tree_move_path(build(), "Bio/Week1", "Bio", new_name="Week 1")
    week = [n for n in t if n["name"] == "Bio"][0]["children"][0]
    assert week["name"] == "Week 1"
    assert week["path"] == "Bio/Week 1"
    assert week["children"][0]["path"] == "Bio/Week 1/cells.pdf"
