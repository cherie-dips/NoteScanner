import fitz  # PyMuPDF
import pytest
from fastapi import HTTPException

from backend import library, llm_pipeline, settings, usage

L1 = "plaksha-university/discrete-maths/class-notes/L1.pdf"
L2 = "plaksha-university/discrete-maths/class-notes/L2.pdf"
CN = "plaksha-university/computer-networks/class-notes/L1.pdf"

PIGEONHOLE = (
    "The pigeonhole principle: if n + 1 pigeons are placed into n holes, then at least one hole "
    "holds two or more pigeons. It is used to prove that some repetition must exist. "
)
GRAPHS = "A graph is a set of vertices joined by edges. A tree is a connected graph that has no cycles. "
TCP = "TCP is a connection-oriented transport protocol: it uses a three-way handshake and retransmits lost segments. "


def text_pdf(*pages: str) -> bytes:
    doc = fitz.open()
    for text in pages:
        page = doc.new_page(width=595, height=842)
        page.insert_textbox(fitz.Rect(40, 40, 555, 800), text, fontsize=10)
    return doc.tobytes()


def handwritten_pdf(pages: int = 1, height: float = 842) -> bytes:
    """Pages of pen strokes only (like OneNote ink), in rows with blank gaps between them."""
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page(width=600, height=height)
        shape = page.new_shape()
        rows = int(height // 60)
        for r in range(rows):
            y = 30 + r * 60
            for c in range(12):
                x = 40 + c * 45
                shape.draw_line((x, y), (x + 30, y + 12))
                shape.finish(color=(0, 0, 0), width=2)
        shape.commit()
    return doc.tobytes()


class FakeBucket:
    def __init__(self):
        self.files: dict[str, tuple[str, bytes]] = {}
        self.downloads: list[str] = []
        self.broken: set[str] = set()

    def put(self, path: str, data: bytes, etag: str = "v1"):
        self.files[path] = (etag, data)

    def list(self):
        return [{"path": p, "etag": e, "size": len(d)} for p, (e, d) in sorted(self.files.items())]

    def download(self, path: str) -> bytes:
        self.downloads.append(path)
        if path in self.broken:
            raise OSError("download failed")
        return self.files[path][1]


@pytest.fixture(autouse=True)
def bucket(monkeypatch):
    b = FakeBucket()
    monkeypatch.setattr(library, "list_source_files", b.list)
    monkeypatch.setattr(library, "download", b.download)
    monkeypatch.setattr(settings, "LIBRARY_OCR", "tesseract")
    library._status_cache.update(at=0.0, value=None)
    yield b
    library._status_cache.update(at=0.0, value=None)


@pytest.fixture(autouse=True)
def fake_answers(monkeypatch):
    """Answer with the context we were given, so tests can see which passages were used."""
    monkeypatch.setattr(llm_pipeline, "sarvam_rag_answer", lambda q, ctx: (f"ANSWER\n{ctx[:600]}", None))


@pytest.fixture
def course(bucket):
    bucket.put(L1, text_pdf(PIGEONHOLE * 4, "Second page. " + PIGEONHOLE * 2))
    bucket.put(L2, text_pdf(GRAPHS * 6))
    bucket.put(CN, text_pdf(TCP * 6))
    summary = library.sync()
    assert summary["indexed"] == 3 and summary["failed"] == 0
    return bucket


def ask(client, user, question, library_path):
    r = client.post("/query_folder", data={"query": question, "library_path": library_path}, headers=user["headers"])
    return r


def test_status_lists_ready_pdfs_without_an_account(client, course):
    j = client.get("/library/status").json()
    assert j["enabled"] is True and j["ready"] == sorted([L1, L2, CN])


def test_question_about_the_open_pdf_uses_it_first(client, user, course):
    j = ask(client, user, "What does the pigeonhole principle say?", L1).json()
    assert j["selection_stage"] == "library_file"
    assert "Course notes: Discrete Maths › Class Notes › L1" in j["answer"]
    md = j["source_documents"][0]["metadata"]
    assert md["path"] == L1 and md["library"] == 1 and md["page"] in (1, 2) and 0 <= md["y"] <= 1


def test_off_topic_question_moves_to_the_rest_of_the_course(client, user, course):
    j = ask(client, user, "What is a tree in graph theory?", L1).json()
    assert j["selection_stage"] == "library_subject"
    assert {d["metadata"]["path"] for d in j["source_documents"]} == {L2}  # never another course
    assert "[Course notes: Discrete Maths › Class Notes › L2]" in j["answer"]  # one page: no page number


def test_multi_page_pdfs_cite_the_page(client, user, course):
    j = ask(client, user, "What does the pigeonhole principle say?", L1).json()
    assert ", page 1]" in j["answer"] or ", page 2]" in j["answer"]


def test_students_own_notes_are_the_last_fallback(client, user, upload, course):
    upload(user["headers"], "ml.md", b"Gradient descent updates weights against the gradient of the loss. " * 8)
    j = ask(client, user, "How does gradient descent update the weights?", L1).json()
    assert j["selection_stage"] == "all_notes" and "ml.md" in j["answer"]


def test_unrelated_own_notes_never_answer_a_course_question(client, user, upload, course):
    upload(user["headers"], "physics.md", b"Newton's second law: force equals mass times acceleration. " * 8)
    r = ask(client, user, "What does the pigeonhole principle say?", "plaksha-university/deep-learning/class-notes/L1.pdf")
    assert r.status_code == 400 and r.json()["detail"] == library.NOT_READY


def test_without_library_path_nothing_changes(client, user, course):
    r = client.post("/query_folder", data={"query": "pigeonhole principle"}, headers=user["headers"])
    assert r.status_code == 400 and "Upload notes" in r.json()["detail"]


def test_paths_outside_the_library_are_ignored(client, user, course):
    r = ask(client, user, "pigeonhole principle", "some-other-bucket/x/y/L1.pdf")
    assert r.status_code == 400 and "Upload notes" in r.json()["detail"]


def test_course_not_indexed_yet_says_so(client, user, course):
    r = ask(client, user, "pigeonhole principle", "plaksha-university/deep-learning/class-notes/L1.pdf")
    assert r.status_code == 400 and r.json()["detail"] == library.NOT_READY


def test_passages_remember_their_page(course):
    got = library._notes(create=False).get(where={"path": L1}, include=["metadatas"])
    pages = {m["page"] for m in got["metadatas"]}
    assert pages == {1, 2}


def test_unchanged_pdfs_are_not_read_again(course):
    course.downloads.clear()
    summary = library.sync()
    assert summary["to_index"] == 0 and summary["up_to_date"] == 3 and course.downloads == []


def test_changed_pdf_is_reindexed_and_removed_pdf_dropped(client, user, course):
    course.put(L2, text_pdf("Hamiltonian cycles visit every vertex exactly once. " * 6), etag="v2")
    del course.files[CN]
    summary = library.sync()
    assert summary["indexed"] == 1 and summary["removed"] == 1
    assert "Hamiltonian" in library.document_text(L2)
    assert library.document_text(CN) == ""
    assert CN not in client.get("/library/status").json()["ready"]
    assert not library._notes(create=False).get(where={"path": CN}, include=[])["ids"]


def test_failed_reindex_keeps_the_previous_version(course):
    course.put(L1, b"new version", etag="v2")
    course.broken.add(L1)
    summary = library.sync()
    assert summary["failed"] == 1
    assert library.indexed_files()[L1]["status"] == "ok"
    assert "pigeonhole" in library.document_text(L1).lower()


def test_handwritten_page_is_cut_into_pieces_and_read_with_ocr(monkeypatch):
    seen = []

    def fake_tesseract(tile):
        seen.append((tile.page, round(tile.y0, 2)))
        return f"handwritten piece {len(seen)} about recurrences"

    monkeypatch.setattr(library, "_tesseract", fake_tesseract)
    pdf = library.read_pdf(handwritten_pdf(height=1700), ocr="tesseract")
    assert pdf.ocr_pieces == len(seen) == 2 and pdf.method == "ocr"
    assert seen[0] == (1, 0.0) and 0.3 < seen[1][1] < 0.7
    assert [(page, round(y, 2)) for _, page, y in pdf.anchors] == seen
    assert pdf.text.startswith("handwritten piece 1")


def test_cuts_fall_on_blank_rows():
    with fitz.open(stream=handwritten_pdf(height=1700), filetype="pdf") as doc:
        page = doc[0]
        (cut,) = library._cut_rows(page, 2)
    # strokes occupy rows y..y+12 every 60pt starting at 30: a blank row is outside every stroke band
    assert not any(30 + r * 60 - 1 <= cut <= 30 + r * 60 + 13 for r in range(50))


def test_typed_pages_are_not_sent_to_ocr(monkeypatch):
    monkeypatch.setattr(library, "_tesseract", lambda tile: pytest.fail("typed text must not be OCR'd"))
    pdf = library.read_pdf(text_pdf(PIGEONHOLE * 3), ocr="tesseract")
    assert pdf.ocr_pieces == 0 and pdf.method == "text" and "pigeonhole" in pdf.text


def test_sarvam_reads_at_most_ten_pieces_per_job(bucket, monkeypatch):
    jobs = []

    def fake_sarvam(pdf_bytes):
        with fitz.open(stream=pdf_bytes, filetype="pdf") as d:
            n = len(d)
        jobs.append(n)
        pages = [f"Sarvam page {k + 1} of job {len(jobs)} explains modular arithmetic" for k in range(n)]
        return "\n\n".join(pages), None, pages

    monkeypatch.setattr(settings, "LIBRARY_OCR", "sarvam")
    monkeypatch.setattr(llm_pipeline, "transcribe_pdf_with_pages", fake_sarvam)
    bucket.put(L1, handwritten_pdf(pages=12))
    summary = library.sync()
    assert jobs == [10, 2]
    assert summary["sarvam_pieces"] == 12 and summary["indexed"] == 1
    text = library.document_text(L1)
    assert text.index("page 1 of job 1") < text.index("page 10 of job 1") < text.index("page 2 of job 2")
    rec = [r for r in usage.records_since(usage.today()) if r.get("user_id") == library.USAGE_ID]
    assert rec and rec[0]["pages_read"] == 12


def test_dry_run_counts_ocr_pieces_and_changes_nothing(bucket, monkeypatch):
    monkeypatch.setattr(library, "_tesseract", lambda tile: pytest.fail("dry run must not read"))
    bucket.put(L1, handwritten_pdf(pages=3))
    bucket.put(L2, text_pdf(GRAPHS * 3))
    summary = library.sync(dry_run=True)
    assert summary["ocr_pieces"] == 3 and summary["to_index"] == 2 and summary["indexed"] == 0
    assert library.indexed_files() == {}


def test_budget_reached_leaves_ocr_files_for_later(bucket, monkeypatch):
    def broke():
        raise HTTPException(status_code=503, detail="budget used")

    monkeypatch.setattr(settings, "LIBRARY_OCR", "sarvam")
    monkeypatch.setattr(usage, "check_budget", broke)
    monkeypatch.setattr(llm_pipeline, "transcribe_pdf_with_pages", lambda b: pytest.fail("no OCR over budget"))
    bucket.put(L1, handwritten_pdf())
    bucket.put(L2, text_pdf(GRAPHS * 3))  # typed: still indexed
    summary = library.sync()
    assert summary["waiting_for_budget"] == 1 and summary["indexed"] == 1
    files = library.indexed_files()
    assert files[L1]["status"] == "pending" and files[L2]["status"] == "ok"
    monkeypatch.setattr(usage, "check_budget", lambda: None)
    monkeypatch.setattr(llm_pipeline, "transcribe_pdf_with_pages", lambda b: ("ink text about sets", None, ["ink text about sets"]))
    assert library.sync()["indexed"] == 1  # the pending file is picked up next time
    assert library.indexed_files()[L1]["status"] == "ok"


def test_study_tools_read_the_library_pdf(client, user, course, monkeypatch):
    seen = {}

    def fake_study(ctx, task, n):
        seen["ctx"] = ctx
        return [{"front": "What is the pigeonhole principle?", "back": "n+1 pigeons, n holes", "source": "notes"}], None

    monkeypatch.setattr(llm_pipeline, "cheap_study_json", fake_study)
    r = client.post("/study/generate", data={"task": "flashcards", "count": 3, "library_path": L1}, headers=user["headers"])
    assert r.status_code == 200, r.text
    assert r.json()["grounded_on_path"] == L1
    assert "--- PRIMARY FILE CONTENT ---" in seen["ctx"] and "pigeonhole" in seen["ctx"].lower()
    assert "Discrete Maths › Class Notes › L2" in seen["ctx"]  # same-course support
    assert "TCP" not in seen["ctx"]  # never another course


def test_summary_of_a_library_pdf(client, user, course, monkeypatch):
    monkeypatch.setattr(llm_pipeline, "topic_summary", lambda ctx: ("Summary: " + ctx[-80:], None))
    r = client.post("/study/summary", data={"library_path": L2, "include_course_context": "false"}, headers=user["headers"])
    assert r.status_code == 200 and "Summary:" in r.json()["summary"]


def test_study_on_a_pdf_that_is_not_ready(client, user, course):
    r = client.post(
        "/study/generate",
        data={"task": "mcq", "library_path": "plaksha-university/discrete-maths/class-notes/L99.pdf"},
        headers=user["headers"],
    )
    assert r.status_code == 400 and r.json()["error"] == library.NOT_READY


def test_deck_from_a_library_pdf(client, user, course):
    cards = '[{"front": "Pigeonhole principle?", "back": "n+1 pigeons in n holes"}]'
    r = client.post("/study/decks", data={"name": "L1", "source_path": L1, "cards": cards}, headers=user["headers"])
    assert r.status_code == 201
    decks = client.get("/study/decks", headers=user["headers"]).json()["decks"]
    assert decks[0]["source_path"] == L1


def test_admin_library_endpoints(client, signup, course, monkeypatch):
    admin = signup("admin@example.com")
    student = signup()
    monkeypatch.setattr(settings, "ADMIN_EMAILS", {"admin@example.com"})
    assert client.get("/admin/library", headers=student["headers"]).status_code == 403
    j = client.get("/admin/library", headers=admin["headers"]).json()
    assert j["by_status"] == {"ok": 3} and len(j["files"]) == 3
    started = []
    monkeypatch.setattr(library, "sync_in_background", lambda force=False: started.append(force) or True)
    assert client.post("/admin/library/sync", data={"force": "true"}, headers=admin["headers"]).json()["started"]
    assert started == [True]
    assert client.post("/admin/library/sync", headers=student["headers"]).status_code == 403


def test_library_ocr_is_not_counted_as_a_student(client, signup, course, monkeypatch):
    usage.record(library.USAGE_ID, pages_read=7)
    admin = signup("admin@example.com")
    monkeypatch.setattr(settings, "ADMIN_EMAILS", {"admin@example.com"})
    j = client.get("/admin/stats", headers=admin["headers"]).json()
    assert j["totals"]["pages_read"] == 7 and j["totals"]["active_users"] == 0


def test_label_and_scope_helpers():
    assert library.label_of(L1) == "Discrete Maths › Class Notes › L1"
    assert library.scope_of(L1) == (L1, "plaksha-university/discrete-maths")
    assert library.scope_of("plaksha-university/discrete-maths") == ("", "plaksha-university/discrete-maths")
    assert library.scope_of("../etc/passwd") == ("", "")
    assert library.scope_of("plaksha-university/../x.pdf") == ("", "")
