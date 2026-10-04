import io
import threading

import fitz
from PIL import Image, ImageDraw, ImageFont

import backend.chroma_store as cs
from conftest import tree_paths


def image_with_text(text: str) -> bytes:
    img = Image.new("RGB", (900, 200), "white")
    draw = ImageDraw.Draw(img)
    font = None
    for candidate in ("/System/Library/Fonts/Supplemental/Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            font = ImageFont.truetype(candidate, 48)
            break
        except OSError:
            continue
    draw.text((20, 60), text, fill="black", font=font or ImageFont.load_default(size=48))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def pdf_with_text(text: str) -> bytes:
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    return doc.tobytes()


def test_upload_is_queued_and_appears_when_ready(client, user, upload):
    h = user["headers"]
    job = upload(h, "newton.md", b"Force equals mass times acceleration. " * 10)
    assert job["path"] == "newton.md" and "searchable" in job["message"]
    assert tree_paths(client, h) == {"newton.md"}
    assert any(j["job_id"] == job["job_id"] for j in client.get("/jobs", headers=h).json()["jobs"])


def test_bad_uploads_are_refused_and_not_added(client, user, upload):
    h = user["headers"]
    r = client.post("/upload_note", data={"path": ""}, files={"file": ("doc.docx", b"PK..")}, headers=h)
    assert r.status_code == 415
    r = client.post("/upload_note", data={"path": ""}, files={"file": ("big.txt", b"x" * (1024 * 1024 + 5))}, headers=h)
    assert r.status_code == 413
    job = upload(h, "empty.txt", b"   ", expect="failed")
    assert "text" in job["error"]
    job = upload(h, "broken.pdf", b"not a pdf", expect="failed")
    assert "damaged" in job["error"]
    assert tree_paths(client, h) == set()


def test_pdf_and_image_text_saved_under_real_names(client, user, upload):
    h, uid = user["headers"], user["user_id"]
    client.post("/create_folder", data={"name": "Bio"}, headers=h)
    upload(h, "notes.txt", b"Mitochondria is the powerhouse of the cell. " * 20, folder="Bio")
    upload(h, "notes.png", image_with_text("Photosynthesis uses chlorophyll"), folder="Bio")
    upload(h, "notes.pdf", pdf_with_text("Eigenvalues satisfy det(A - lambda I) = 0."), folder="Bio")
    assert "photosynthesis" in cs.user_notes_concat_text_for_path(uid, "Bio/notes.png").lower()
    assert "Eigenvalues" in cs.user_notes_concat_text_for_path(uid, "Bio/notes.pdf")
    txt_before = cs.user_notes_concat_text_for_path(uid, "Bio/notes.txt")
    assert "Mitochondria" in txt_before

    for name in ("notes.png", "notes.pdf"):
        assert client.post("/delete_path", data={"path": f"Bio/{name}", "kind": "file"}, headers=h).status_code == 200
    assert cs.user_notes_concat_text_for_path(uid, "Bio/notes.txt") == txt_before
    assert tree_paths(client, h) == {"Bio/notes.txt"}


def test_rename_file_and_folder(client, user, upload):
    h, uid = user["headers"], user["user_id"]
    client.post("/create_folder", data={"name": "Chem"}, headers=h)
    upload(h, "acids.txt", b"Acids donate protons. " * 20, folder="Chem")
    client.post("/file_meta", data={"path": "Chem/acids.txt", "is_primary_authority": "true"}, headers=h)

    r = client.post("/rename_path", data={"path": "Chem/acids.txt", "new_name": "acids and bases.txt"}, headers=h)
    assert r.status_code == 200 and r.json()["path"] == "Chem/acids and bases.txt"
    assert "donate protons" in cs.user_notes_concat_text_for_path(uid, "Chem/acids and bases.txt")
    assert client.get("/file_meta", headers=h).json()["meta"]["Chem/acids and bases.txt"]["is_primary_authority"]

    r = client.post("/rename_path", data={"path": "Chem", "new_name": "Chemistry"}, headers=h)
    assert r.status_code == 200 and r.json()["kind"] == "folder"
    assert tree_paths(client, h) == {"Chemistry/acids and bases.txt"}
    assert "donate protons" in cs.user_notes_concat_text_for_path(uid, "Chemistry/acids and bases.txt")
    assert client.get("/file_text", params={"path": "Chemistry/acids and bases.txt"}, headers=h).json()["text"].startswith("Acids")

    assert client.post("/rename_path", data={"path": "Chemistry", "new_name": "a/b"}, headers=h).status_code == 400
    client.post("/create_folder", data={"name": "Physics"}, headers=h)
    assert client.post("/rename_path", data={"path": "Physics", "new_name": "Chemistry"}, headers=h).status_code == 400


def test_move_and_delete_folder(client, user, upload):
    h, uid = user["headers"], user["user_id"]
    client.post("/create_folder", data={"name": "Bio"}, headers=h)
    client.post("/create_folder", data={"path": "Bio", "name": "Week1"}, headers=h)
    client.post("/create_folder", data={"name": "Archive"}, headers=h)
    upload(h, "cells.txt", b"Cells are the basic unit of life. " * 20, folder="Bio/Week1")
    assert client.post("/move_path", data={"from_path": "Bio", "to_folder": "Bio/Week1"}, headers=h).status_code == 400
    r = client.post("/move_path", data={"from_path": "Bio", "to_folder": "Archive"}, headers=h)
    assert r.status_code == 200 and r.json()["path"] == "Archive/Bio"
    assert "basic unit" in cs.user_notes_concat_text_for_path(uid, "Archive/Bio/Week1/cells.txt")
    assert client.post("/delete_path", data={"path": "Archive", "kind": "folder"}, headers=h).status_code == 200
    assert cs.user_notes_concat_text_for_path(uid, "Archive/Bio/Week1/cells.txt") == ""
    assert cs.user_document_get(uid, "Archive/Bio/Week1/cells.txt") is None


def test_view_and_edit_text(client, user, upload):
    h, uid = user["headers"], user["user_id"]
    upload(h, "ocr.txt", b"The mitocondria is the powerhose of the cell.")
    r = client.get("/file_text", params={"path": "ocr.txt"}, headers=h)
    assert r.json()["text"].startswith("The mitocondria")
    fixed = "The mitochondria is the powerhouse of the cell."
    r = client.post("/file_text", data={"path": "ocr.txt", "text": fixed}, headers=h)
    assert r.status_code == 200 and r.json()["chunks"] == 1
    assert client.get("/file_text", params={"path": "ocr.txt"}, headers=h).json()["text"] == fixed
    assert cs.user_notes_concat_text_for_path(uid, "ocr.txt") == fixed
    assert client.get("/file_text", params={"path": "missing.txt"}, headers=h).status_code == 404


def test_new_empty_file_can_be_written(client, user):
    h = user["headers"]
    assert client.post("/create_file", data={"name": "my notes"}, headers=h).json()["path"] == "my notes.txt"
    assert client.post("/create_file", data={"name": "my notes.txt"}, headers=h).status_code == 400
    assert client.get("/file_text", params={"path": "my notes.txt"}, headers=h).json()["text"] == ""
    client.post("/file_text", data={"path": "my notes.txt", "text": "Ohm's law: V = IR."}, headers=h)
    r = client.post("/query_folder", data={"query": "What is Ohm's law?"}, headers=h)
    assert r.status_code in (200, 503)  # 503 = no AI key in tests; the search itself succeeded


def test_parallel_writes_keep_every_change(client, user):
    h = user["headers"]
    threads = [
        threading.Thread(target=lambda i=i: client.post("/create_folder", data={"name": f"F{i}"}, headers=h))
        for i in range(12)
    ]
    [t.start() for t in threads]
    [t.join() for t in threads]
    names = {n["name"] for n in client.get("/list_tree", headers=h).json()["tree"]}
    assert names == {f"F{i}" for i in range(12)}


def test_failed_tree_read_never_wipes_tree(client, user, monkeypatch):
    h = user["headers"]
    client.post("/create_folder", data={"name": "Keep"}, headers=h)

    def outage(*a, **k):
        raise ConnectionError("simulated outage")

    monkeypatch.setattr(cs, "_vfs_read_json", outage)
    r = client.post("/create_folder", data={"name": "DuringOutage"}, headers=h)
    assert r.status_code == 500 and r.headers["content-type"].startswith("application/json")
    monkeypatch.undo()
    assert [n["name"] for n in client.get("/list_tree", headers=h).json()["tree"]] == ["Keep"]


def test_delete_all_notes(client, user, upload):
    h = user["headers"]
    upload(h, "a.txt", b"Some text to search. " * 5)
    r = client.delete("/notes", headers=h)
    assert r.status_code == 200
    assert tree_paths(client, h) == set()
    assert cs.user_document_get(user["user_id"], "a.txt") is None


def test_upload_reply_is_always_queued(client, user):
    """The reply is built before the worker starts, so even an instant failure replies 'queued'."""
    for i in range(10):
        r = client.post("/upload_note", data={"path": ""}, files={"file": (f"e{i}.txt", b"  ")}, headers=user["headers"])
        assert r.status_code == 202 and r.json()["status"] == "queued" and r.json()["error"] is None
