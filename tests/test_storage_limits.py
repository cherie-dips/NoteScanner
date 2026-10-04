"""Large libraries must stay within Chroma Cloud's limits (300 results, 300 writes, 16 KB docs, 128-byte ids)."""
import backend.chroma_store as cs
from backend.ingest_api import ingest_text_for_path
from conftest import tree_paths


def test_big_folder_tree_is_split_into_parts(client, user):
    h = user["headers"]
    for i in range(120):  # ~120 folders with long names: well over 16 KB of tree JSON
        r = client.post("/create_folder", data={"path": "", "name": f"Course {i:03d} - Advanced Topics in Something Long"}, headers=h)
        assert r.status_code == 200, r.text
    tree = client.get("/list_tree", headers=h).json()["tree"]
    assert len(tree) == 120
    raw = cs._read_parts(cs.get_user_vfs_collection(user["user_id"]), cs.VFS_TREE_DOC_ID)
    assert len(raw.encode()) > 16384  # bigger than one Chroma Cloud document, so it must be in parts


def test_long_paths_fit_id_limit(client, user, upload):
    h = user["headers"]
    folder = "Semester 3 - Machine Learning and Pattern Recognition"
    client.post("/create_folder", data={"path": "", "name": folder}, headers=h)
    name = "Lecture 12 - Support Vector Machines, Kernels and the Dual Optimisation Problem.md"
    upload(h, name, b"Support vector machines maximise the margin between classes. " * 30, folder=folder)
    assert f"{folder}/{name}" in tree_paths(client, h)
    r = client.get("/file_text", params={"path": f"{folder}/{name}"}, headers=h)
    assert "maximise the margin" in r.json()["text"]


def test_file_with_more_than_300_chunks(client, user):
    uid = user["user_id"]
    text = "".join(f"Sentence number {i} about thermodynamics and entropy. " for i in range(6000))
    result = ingest_text_for_path(uid, "Physics/huge.txt", "huge.txt", text)
    assert result["chunks_created"] > 300
    # Reading it back pages through all chunks and removes the overlap between them.
    joined = cs.user_notes_concat_text_for_path(uid, "Physics/huge.txt")
    assert "Sentence number 0 " in joined and "Sentence number 5999 " in joined
    assert joined.count("Sentence number 3000 ") == 1
    # Moving it moves every chunk (in batches).
    from backend.ingest_api import chunks_relocate_path

    moved = chunks_relocate_path(uid, "Physics/huge.txt", "Archive/huge.txt")
    assert moved == result["chunks_created"]
    assert cs.user_notes_concat_text_for_path(uid, "Physics/huge.txt") == ""
    # Deleting removes every chunk.
    assert cs.delete_user_notes_by_paths(uid, ["Archive/huge.txt"]) == moved


def test_full_text_is_stored_in_parts(user):
    uid = user["user_id"]
    text = "x" * 50000 + " END"
    cs.user_document_upsert(uid, "Notes/long.txt", text, "long.txt")
    doc = cs.user_document_get(uid, "Notes/long.txt")
    assert doc["content"] == text and not doc["truncated"]
    cs.user_document_upsert(uid, "Notes/long.txt", "short now", "long.txt")
    assert cs.user_document_get_content(uid, "Notes/long.txt") == "short now"
    assert cs.user_document_rename_path(uid, "Notes/long.txt", "Notes/renamed.txt")
    assert cs.user_document_get(uid, "Notes/long.txt") is None
    assert cs.user_document_get_content(uid, "Notes/renamed.txt") == "short now"


def test_old_single_record_documents_still_readable(user):
    """Text saved by the previous version (one record, id 'user|path') must still be found."""
    uid = user["user_id"]
    col = cs.get_user_documents_collection()
    col.add(
        ids=[f"{uid}|Old/notes.txt"],
        documents=["old style text"],
        embeddings=[cs._dummy_embedding()],
        metadatas=[{"user_id": uid, "path": "Old/notes.txt", "original_filename": "notes.txt", "truncated": 0}],
    )
    assert cs.user_document_get_content(uid, "Old/notes.txt") == "old style text"
    cs.user_document_upsert(uid, "Old/notes.txt", "new text", "notes.txt")
    assert cs.user_document_get_content(uid, "Old/notes.txt") == "new text"
    assert not col.get(ids=[f"{uid}|Old/notes.txt"])["ids"]  # the old record was replaced, not duplicated


def test_old_single_record_tree_still_readable(user):
    uid = user["user_id"]
    col = cs.get_user_vfs_collection(uid)
    col.upsert(
        ids=[cs.VFS_TREE_DOC_ID],
        documents=['[{"type": "file", "name": "a.txt", "path": "a.txt"}]'],
        embeddings=[cs._dummy_embedding()],
        metadatas=[{"kind": "vfs_tree"}],
    )
    assert cs.vfs_get_tree(uid) == [{"type": "file", "name": "a.txt", "path": "a.txt"}]
