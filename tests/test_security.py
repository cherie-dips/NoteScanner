import backend.chroma_store as cs
from backend import legacy_migration
from backend.ingest_api import ingest_text_for_path


def test_health_and_config(client):
    assert client.get("/health").json() == {"status": "ok"}
    cfg = client.get("/config").json()
    assert set(cfg) == {
        "password_reset_enabled", "onenote_configured", "max_upload_mb", "max_chat_files",
        "reminders_available", "languages",
    }


def test_cors_only_for_allowed_sites(client):
    r = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers
    r = client.get("/health", headers={"Origin": "https://cherie-dips.github.io"})
    assert r.headers.get("access-control-allow-origin") == "https://cherie-dips.github.io"


def test_crash_returns_json_with_cors(client, user, monkeypatch):
    from backend import paths

    monkeypatch.setattr(paths, "vfs_get_tree", lambda uid: (_ for _ in ()).throw(RuntimeError("boom")))
    r = client.get("/list_tree", headers={**user["headers"], "Origin": "https://cherie-dips.github.io"})
    assert r.status_code == 500 and r.json()["error"]
    assert r.headers.get("access-control-allow-origin")


def test_onenote_callback_escapes_html(client):
    r = client.get("/integrations/onenote/callback", params={"error": "x", "error_description": "<script>alert(1)</script>"})
    assert "<script>" not in r.text and "&lt;script&gt;" in r.text


def test_users_cannot_see_each_other(client, signup, upload):
    a, b = signup(), signup()
    upload(a["headers"], "secret.txt", b"My private notes about cryptography. " * 5)
    assert client.get("/list_tree", headers=b["headers"]).json()["tree"] == []
    assert client.get("/file_text", params={"path": "secret.txt"}, headers=b["headers"]).status_code == 404


def test_legacy_twin_data_moves_to_real_paths(client, user):
    """Uploads from before the fix stored image/PDF text under a hidden '<name>.txt' path."""
    uid = user["user_id"]
    cs.vfs_set_tree(uid, [{"type": "folder", "name": "C", "path": "C", "children": [
        {"type": "file", "name": "old.png", "path": "C/old.png"},
        {"type": "file", "name": "lec.pdf", "path": "C/lec.pdf"},
        {"type": "file", "name": "own.pdf", "path": "C/own.pdf"},
        {"type": "file", "name": "own.txt", "path": "C/own.txt"},
    ]}])
    ingest_text_for_path(uid, "C/old.txt", "old.png", "Legacy image text about enzymes.")
    cs.user_document_upsert(uid, "C/old.txt", "Legacy image text about enzymes.", "old.png")
    ingest_text_for_path(uid, "C/lec.pdf", "lec.pdf", "Legacy pdf chunk text.")
    cs.user_document_upsert(uid, "C/lec.txt", "Legacy pdf full text.", "lec.pdf")
    ingest_text_for_path(uid, "C/own.txt", "own.txt", "A real text file.")
    cs.user_document_upsert(uid, "C/own.txt", "A real text file.", "own.txt")
    legacy_migration._checked_users.discard(uid)

    client.get("/list_tree", headers=user["headers"])

    assert "enzymes" in cs.user_notes_concat_text_for_path(uid, "C/old.png")
    assert cs.user_notes_concat_text_for_path(uid, "C/old.txt") == ""
    assert "enzymes" in cs.user_document_get_content(uid, "C/old.png")
    assert cs.user_document_get_content(uid, "C/lec.pdf") == "Legacy pdf full text."
    assert cs.user_document_get_content(uid, "C/lec.txt") is None
    assert cs.user_document_get_content(uid, "C/own.txt") == "A real text file."
    assert cs.vfs_get_storage_version(uid) == legacy_migration.STORAGE_VERSION
