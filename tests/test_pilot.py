"""Pilot features: consent, starter notes, usage/cost/budget, alerts, feedback/admin, preferences,
reminders, Today dashboard, exam mode, page numbers."""
import datetime as dt
import json
import time
import zipfile

import fitz
import pytest

import backend.chroma_store as cs
from backend import alerts, llm_pipeline, mailer, pages, reminders, settings, starter_notes, usage
from conftest import tree_paths


@pytest.fixture(autouse=True)
def clean_usage_cache():
    usage.reset_cache()
    yield
    usage.reset_cache()


def usage_today(user_id: str) -> dict:
    recs = usage.records_since(usage.today(), user_id)
    return recs[0] if recs else {}


def test_signup_requires_privacy_consent(client):
    r = client.post("/register", data={"email": "c@example.com", "password": "goodpass123"})
    assert r.status_code == 400 and "privacy" in r.json()["error"]
    r = client.post("/register", data={"email": "c@example.com", "password": "goodpass123", "accepted_privacy": "true"})
    meta = cs.user_get_by_id(r.json()["user_id"])["meta"]
    assert meta["privacy_version"] == settings.PRIVACY_VERSION and meta["privacy_accepted_at"] > 0


def test_starter_notes_answer_a_first_question(client, user, monkeypatch):
    starter_notes.add_starter_notes(user["user_id"])
    assert tree_paths(client, user["headers"]) == {f"{starter_notes.STARTER_FOLDER}/{n}" for n, _ in starter_notes.STARTER_FILES}
    monkeypatch.setattr(llm_pipeline, "sarvam_rag_answer", lambda q, ctx, **kw: (ctx[:300], None))
    j = client.post("/query_folder", data={"query": "What is Newton's second law?"}, headers=user["headers"]).json()
    assert "Newton's laws of motion" in j["answer"]


def test_starter_notes_use_the_name_the_student_signed_up_under(client, user, signup):
    starter_notes.add_starter_notes(user["user_id"], "Study AI")
    welcome = f"{starter_notes.STARTER_FOLDER}/Welcome to Study AI.md"
    assert welcome in tree_paths(client, user["headers"])
    text = client.get("/file_text", params={"path": welcome}, headers=user["headers"]).json()["text"]
    assert text.startswith("# Welcome to Study AI") and "NoteScanner" not in text
    other = signup()
    starter_notes.add_starter_notes(other["user_id"], "Something else")  # only known names are used
    assert f"{starter_notes.STARTER_FOLDER}/Welcome to NoteScanner.md" in tree_paths(client, other["headers"])


def test_usage_is_counted(client, user, upload, monkeypatch):
    h, uid = user["headers"], user["user_id"]
    monkeypatch.setattr(llm_pipeline, "_sarvam_chat_complete", lambda *a, **k: (llm_pipeline._add_usage(120, 30), ("Answer.", None))[1])
    upload(h, "a.txt", b"Photosynthesis turns light into chemical energy. " * 5)
    client.post("/query_folder", data={"query": "What is photosynthesis?"}, headers=h)
    rec = usage_today(uid)
    assert rec["uploads"] == 1 and rec["questions"] == 1
    assert rec["prompt_tokens"] == 120 and rec["completion_tokens"] == 30
    client.post("/feedback", data={"kind": "answer", "rating": "up", "question": "q"}, headers=h)
    assert usage_today(uid)["ratings_up"] == 1


def test_monthly_budget_pauses_paid_ai(client, user, upload, monkeypatch):
    h, uid = user["headers"], user["user_id"]
    upload(h, "a.txt", b"Photosynthesis turns light into chemical energy. " * 5)
    monkeypatch.setattr(settings, "AI_PRICE_PER_1K_INPUT_TOKENS", 1.0)
    monkeypatch.setattr(settings, "AI_MONTHLY_BUDGET", 5.0)
    usage.record(uid, prompt_tokens=6000)  # 6.0 spent > 5.0 budget
    r = client.post("/query_folder", data={"query": "What is photosynthesis?"}, headers=h)
    assert r.status_code == 503 and "budget" in r.json()["detail"]
    r = client.post("/upload_note", data={"path": ""}, files={"file": ("b.txt", b"more")}, headers=h)
    assert r.status_code == 503
    deck = client.post("/study/decks", data={"name": "d", "cards": json.dumps([{"front": "q", "back": "a"}])}, headers=h)
    assert deck.status_code == 201  # non-AI features keep working


def test_alerts_are_throttled(client, user, monkeypatch):
    sent = []
    monkeypatch.setattr(settings, "ALERT_WEBHOOK_URL", "https://hooks.example/test")
    monkeypatch.setattr(alerts, "_post", lambda text: sent.append(text))
    monkeypatch.setattr(alerts, "_last_sent", {})
    from backend import paths

    monkeypatch.setattr(paths, "vfs_get_tree", lambda uid: (_ for _ in ()).throw(RuntimeError("boom")))
    for _ in range(3):
        assert client.get("/list_tree", headers=user["headers"]).status_code == 500
    time.sleep(0.2)
    assert len(sent) == 1 and "boom" in sent[0]


def test_feedback_and_admin_pages(client, signup, monkeypatch):
    student, admin = signup(), signup("boss@example.com")
    h = student["headers"]
    assert client.post("/feedback", data={"kind": "general", "comment": ""}, headers=h).status_code == 400
    assert client.post("/feedback", data={"kind": "answer", "rating": "down", "comment": "wrong formula", "question": "F?"}, headers=h).status_code == 201
    assert client.post("/feedback", data={"kind": "general", "comment": "Love it"}, headers=h).status_code == 201

    assert client.get("/admin/stats", headers=h).status_code == 403
    monkeypatch.setattr(settings, "ADMIN_EMAILS", {"boss@example.com"})
    assert client.get("/me", headers=admin["headers"]).json()["is_admin"] is True
    stats = client.get("/admin/stats", params={"days": 7}, headers=admin["headers"]).json()
    assert stats["totals"]["ratings_down"] == 1 and stats["totals"]["helpful_share"] == 0.0
    assert stats["totals"]["active_users"] == 1 and len(stats["daily"]) == 7
    assert stats["totals"]["estimated_cost"] is None  # no prices set
    rows = client.get("/admin/feedback", headers=admin["headers"]).json()["feedback"]
    assert [r["comment"] for r in rows] == ["Love it", "wrong formula"] or [r["comment"] for r in rows] == ["wrong formula", "Love it"]
    assert all(r["user_email"] == student["email"] for r in rows)


def test_preferences_and_answer_language(client, user, upload, monkeypatch):
    h = user["headers"]
    prefs = client.get("/account/preferences", headers=h).json()
    assert prefs["answer_language"] == "English" and "Hindi" in prefs["languages"] and prefs["reminders_available"] is False
    assert client.post("/account/preferences", data={"answer_language": "Klingon"}, headers=h).status_code == 400
    prefs = client.post("/account/preferences", data={"answer_language": "Hindi", "reminders": "true"}, headers=h).json()
    assert prefs["answer_language"] == "Hindi" and prefs["reminders"] is True
    assert client.get("/me", headers=h).json()["preferences"]["answer_language"] == "Hindi"

    seen = {}
    monkeypatch.setattr(llm_pipeline, "sarvam_rag_answer", lambda q, ctx, **kw: (seen.update(kw), ("ok", None))[1])
    upload(h, "a.txt", b"Photosynthesis turns light into chemical energy. " * 5)
    client.post("/query_folder", data={"query": "What is photosynthesis?"}, headers=h)
    assert seen == {"language": "Hindi"}

    prompts = []
    monkeypatch.setattr(llm_pipeline, "_sarvam_chat_complete", lambda messages, **k: (prompts.append(messages[-1]["content"]), ("not json", None))[1])
    client.post("/study/generate", data={"task": "flashcards", "opened_file_path": "a.txt"}, headers=h)
    assert "Write all of your output in Hindi" in prompts[0]


def test_daily_reminders(user, signup, monkeypatch):
    other = signup()
    for u in (user, other):
        cs.user_update_metadata(u["user_id"], pref_reminders=1)
    from backend import study_store

    study_store.create_deck(user["user_id"], "Physics", "", [{"front": "F?", "back": "ma"}])  # due now
    sent = []
    monkeypatch.setattr(mailer, "is_configured", lambda: True)
    monkeypatch.setattr(mailer, "send_email", lambda to, subject, body: sent.append((to, subject, body)))
    tz = usage.tz()
    morning = dt.datetime.combine(usage.today(), dt.time(settings.REMINDER_HOUR, 30), tzinfo=tz).timestamp()
    early = dt.datetime.combine(usage.today(), dt.time(max(settings.REMINDER_HOUR - 2, 0)), tzinfo=tz).timestamp()
    if settings.REMINDER_HOUR >= 2:
        assert reminders.send_due_reminders(early) == 0
    assert reminders.send_due_reminders(morning) == 1  # only the user with due cards
    assert sent[0][0] == user["email"] and "1 flashcard is due" in sent[0][2]
    assert reminders.send_due_reminders(morning + 60) == 0  # once per day


def test_today_dashboard_and_weak_topics(client, user):
    h = user["headers"]
    cards = [{"front": f"Q{i}", "back": f"A{i}"} for i in range(3)]
    deck = client.post("/study/decks", data={"name": "Bio", "cards": json.dumps(cards)}, headers=h).json()
    due = client.get(f"/study/decks/{deck['deck_id']}/cards", params={"due_only": "true"}, headers=h).json()["cards"]
    client.post(f"/study/cards/{due[0]['card_id']}/review", data={"grade": "good"}, headers=h)
    client.post("/study/mcq_results", data={"path": "Bio/cells.md", "correct": 1, "total": 4}, headers=h)
    client.post("/study/mcq_results", data={"path": "Bio/easy.md", "correct": 4, "total": 4}, headers=h)
    assert client.post("/study/mcq_results", data={"path": "x", "correct": 5, "total": 4}, headers=h).status_code == 400

    d = client.get("/study/dashboard", headers=h).json()
    assert d["due_today"] == 2 and d["reviewed_today"] == 1 and d["streak_days"] == 1 and d["decks"] == 1
    assert [t["path"] for t in d["weak_topics"]] == ["Bio/cells.md"] and d["weak_topics"][0]["accuracy"] == 0.25
    assert len(d["activity"]) == 14 and d["activity"][-1]["reviews"] == 1
    assert d["exam"] is None


def test_streak_counts_consecutive_days(client, user):
    uid = user["user_id"]
    col = usage._collection()
    for back in (1, 2, 4):  # yesterday, the day before, and an older day after a gap
        day = usage.today() - dt.timedelta(days=back)
        col.upsert(
            ids=[cs.short_id("u", uid, day.isoformat())], documents=[""], embeddings=[cs._dummy_embedding()],
            metadatas=[{"user_id": uid, "day": day.isoformat(), "day_num": usage.day_num(day), "month": day.isoformat()[:7], "reviews": 3}],
        )
    assert client.get("/study/dashboard", headers=user["headers"]).json()["streak_days"] == 2


def test_exam_plan_and_mock_test(client, user, upload, monkeypatch):
    h = user["headers"]
    client.post("/create_folder", data={"name": "Physics"}, headers=h)
    upload(h, "forces.md", b"Newton's second law: force equals mass times acceleration. " * 5, folder="Physics")
    upload(h, "energy.md", b"Kinetic energy is one half m v squared. " * 5, folder="Physics")

    past = (usage.today() - dt.timedelta(days=1)).isoformat()
    assert client.post("/study/exam_plan", data={"course_path": "Physics", "exam_date": past}, headers=h).status_code == 400
    exam_day = (usage.today() + dt.timedelta(days=5)).isoformat()
    plan = client.post("/study/exam_plan", data={"course_path": "Physics", "exam_date": exam_day}, headers=h).json()
    assert plan["days_left"] == 5 and plan["plan"][-1]["date"] == exam_day
    assert "mock test" in " ".join(plan["plan"][-2]["tasks"]).lower()
    d = client.get("/study/dashboard", headers=h).json()
    assert d["exam"]["days_left"] == 5 and d["exam"]["today"][0].startswith("Revise")
    assert len(client.get("/study/exams", headers=h).json()["exams"]) == 1

    item = {"question": "F = ?", "options": ["ma", "mv", "m/a", "a/m"], "answer_index": 0, "explanation": "2nd law", "source": "notes"}
    contexts = []
    monkeypatch.setattr(llm_pipeline, "_sarvam_chat_complete", lambda messages, **k: (contexts.append(messages[-1]["content"]), (json.dumps([item] * 10), None))[1])
    r = client.post("/study/exam", data={"course_path": "Physics", "count": 10}, headers=h).json()
    assert len(r["items"]) == 10 and set(r["files_used"]) == {"Physics/forces.md", "Physics/energy.md"}
    assert "Kinetic energy" in contexts[0] and "force equals" in contexts[0]
    assert client.post("/study/exam", data={"course_path": "Empty"}, headers=h).status_code == 400

    assert client.delete(f"/study/exams/{plan['exam_id']}", headers=h).status_code == 200
    assert client.get("/study/exams", headers=h).json()["exams"] == []


def _three_page_pdf() -> bytes:
    """Three pages, each long enough to fill its own search chunks."""
    doc = fitz.open()
    topics = ("Vectors and scalars have different rules for adding.", "Projectile motion follows a parabola under gravity.",
              "Circular motion needs a centripetal force toward the centre.")
    for topic in topics:
        page = doc.new_page()
        for line in range(18):
            page.insert_text((72, 60 + line * 36), f"{topic} Note {line}.")
    return doc.tobytes()


def test_page_numbers_in_sources(client, user, upload, monkeypatch):
    h, uid = user["headers"], user["user_id"]
    upload(h, "mechanics.pdf", _three_page_pdf())
    metas = cs.get_user_collection(uid).get(where={"path": "mechanics.pdf"}, include=["metadatas"])["metadatas"]
    assert sorted({m["page"] for m in metas}) == [1, 2, 3]
    monkeypatch.setattr(llm_pipeline, "sarvam_rag_answer", lambda q, ctx, **kw: (ctx, None))
    j = client.post("/query_folder", data={"query": "What force does circular motion need?", "opened_file_path": "mechanics.pdf"}, headers=h).json()
    top = j["source_documents"][0]
    assert "Circular motion" in top["content"] and top["metadata"]["page"] == 3
    assert "[mechanics.pdf, page 3]" in j["answer"]
    client.post("/rename_path", data={"path": "mechanics.pdf", "new_name": "mech.pdf"}, headers=h)
    moved = cs.get_user_collection(uid).get(where={"path": "mech.pdf"}, include=["metadatas"])["metadatas"]
    assert all("page" in m for m in moved)


def test_page_chunks_point_at_the_right_page():
    page_texts = [f"Page {i} topic {'alpha beta gamma delta ' * 60}" for i in range(1, 4)]
    text, starts = pages.join_pages(page_texts)
    assert [p for _, p in starts] == [1, 2, 3]
    from backend.ingest_api import _chunk_text_with_pages

    chunks, chunk_pages = _chunk_text_with_pages(text, starts)
    for chunk, page in zip(chunks, chunk_pages):
        first_marker = chunk.split("Page ")[1][0] if chunk.startswith("Page ") else None
        if first_marker:
            assert int(first_marker) == page
    assert chunk_pages[0] == 1 and chunk_pages[-1] == 3


def test_page_starts_found_in_sarvam_markdown():
    markdown = "# Notes\n\n**Vectors** have size and direction.\n\n## Projectiles\nA projectile follows a parabola.\n\nCircular motion needs a centripetal force."
    page_texts = ["Vectors have size and direction.", "Projectiles A projectile follows a parabola.", "Circular motion needs a centripetal force."]
    starts = pages.anchor_page_starts(markdown, page_texts)
    assert [p for _, p in starts] == [1, 2, 3]
    assert pages.page_at(markdown.index("A projectile"), starts) == 2
    assert pages.page_at(markdown.index("Circular"), starts) == 3


def test_sarvam_zip_page_texts(tmp_path):
    zpath = tmp_path / "out.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("document.md", "# Doc")
        z.writestr("metadata/page_002.json", json.dumps({"blocks": [{"text": "Second page text"}]}))
        z.writestr("metadata/page_001.json", json.dumps({"blocks": [{"text": "First page"}, {"text": "more"}]}))
        z.writestr("manifest.json", "{}")
    assert llm_pipeline._page_texts_from_output_zip(str(zpath)) == ["First page more", "Second page text"]


def test_sarvam_figures_are_not_kept_as_text(tmp_path):
    image = "![Image](data:image/jpeg;base64," + "QUJD" * 5000 + ")"
    zpath = tmp_path / "out.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("document.md", f"TCP handshake\n\n{image}\n\nSYN, SYN-ACK, ACK")
        z.writestr("metadata/page_001.json", json.dumps({"blocks": [{"text": f"TCP handshake {image}"}]}))
    md = llm_pipeline._extract_markdown_from_output_zip(str(zpath))
    assert md == "TCP handshake\n\n[figure]\n\nSYN, SYN-ACK, ACK"
    assert llm_pipeline._page_texts_from_output_zip(str(zpath)) == ["TCP handshake [figure]"]
    html = 'x <img alt="a" src="data:image/png;base64,QUJD"> y'
    assert llm_pipeline.strip_inline_images(html) == "x [figure] y"


def test_delete_account_removes_usage_and_feedback(client, user):
    h, uid = user["headers"], user["user_id"]
    client.post("/feedback", data={"kind": "general", "comment": "hi"}, headers=h)
    usage.record(uid, questions=1)
    client.post("/account/delete", data={"password": "goodpass123"}, headers=h)
    assert usage.records_since(usage.today(), uid) == []
    from backend import feedback

    assert all(r.get("user_id") != uid for r in feedback.recent(100))
