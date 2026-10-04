"""Study tools: flashcards, MCQs, summaries, saved decks, the Today dashboard and exam mode."""
import datetime as dt
import json
import re
import time

from fastapi import APIRouter, Form
from fastapi.responses import JSONResponse, Response

from backend import alerts, llm_pipeline, preferences, rate_limit, study_store, usage
from backend.chroma_store import user_document_get_content, user_notes_concat_text_for_path
from backend.deps import SignedInUser
from backend.paths import file_paths, get_vfs_tree_clean, strip_storage_path
from backend.settings import LIMIT_DECK_CHANGES_PER_USER, LIMIT_STUDY_PER_DAY, LIMIT_STUDY_PER_USER

router = APIRouter()


def _study_text_for_path(user_id: str, rel_path: str) -> str:
    """Prefer Chroma chunk text (same as RAG), fall back to user_documents."""
    p = strip_storage_path(rel_path or "")
    if not p:
        return ""
    chunk = (user_notes_concat_text_for_path(user_id, p) or "").strip()
    if chunk:
        return chunk
    return (user_document_get_content(user_id, p) or "").strip()


def _build_study_scope_context(
    user_id: str,
    opened_file_path: str,
    course_path: str,
    focus_query: str,
    include_course_context: bool,
) -> tuple[str | None, str | None]:
    """Return context strictly from opened file and optionally same course folder."""
    open_rel = strip_storage_path(opened_file_path or "")
    if not open_rel:
        return None, "Open a file first. Study generation uses the active file context."

    all_paths = file_paths(get_vfs_tree_clean(user_id))
    if open_rel not in set(all_paths):
        return None, f"Active file '{open_rel}' was not found in your notes."

    root_course = strip_storage_path(course_path or "")
    if not root_course:
        root_course = open_rel.split("/", 1)[0] if "/" in open_rel else open_rel
    # Root course is only a top-level folder scope, never a file path.
    root_course = root_course.split("/", 1)[0] if root_course else ""

    open_text = (_study_text_for_path(user_id, open_rel) or "").strip()
    if not open_text:
        return None, (
            f"No text found for '{open_rel}'. Open it in Text view to add some, or upload it again."
        )

    focus = (focus_query or "").strip() or "key ideas for exam"
    parts = [
        f"Focus: {focus}",
        f"Active course folder: {root_course or '(root)'}",
        f"Primary file: {open_rel}",
        "",
        "--- PRIMARY FILE CONTENT ---",
        open_text[:35000],
    ]

    if include_course_context and root_course:
        prefix = root_course + "/"
        extra_paths = [p for p in all_paths if p != open_rel and (p == root_course or p.startswith(prefix))]
        if extra_paths:
            budget = 25000
            extras: list[str] = []
            for p in extra_paths:
                if budget <= 0:
                    break
                t = _study_text_for_path(user_id, p)
                if not t:
                    continue
                chunk = t[: min(len(t), 3500, budget)]
                if not chunk.strip():
                    continue
                extras.append(f"--- COURSE SUPPORTING FILE: {p} ---\n{chunk}")
                budget -= len(chunk)
            if extras:
                parts.append("")
                parts.append("--- OPTIONAL SAME-COURSE SUPPORT ---")
                parts.append("\n\n".join(extras))

    return "\n".join(parts)[:60000], None


def _limit_study(user_id: str) -> None:
    rate_limit.per_user(
        user_id,
        ("study", LIMIT_STUDY_PER_USER, None),
        ("study-day", LIMIT_STUDY_PER_DAY, "flashcard, quiz and summary requests"),
    )
    usage.check_budget()


def _run_study_ai(user_id: str, fn, *args):
    """Call a study AI function in the student's language and count its use. Returns (data, err)."""
    llm_pipeline.take_usage()
    data, err = fn(*args, **preferences.language_kwargs(user_id))
    usage.record(user_id, study_requests=1, **llm_pipeline.take_usage())
    if err:
        alerts.notify("ai-unavailable", f"Study generation failed: {err}")
    return data, err


@router.post("/study/generate")
def study_generate(
    task: str = Form("flashcards"),
    count: int = Form(5),
    focus_query: str = Form("key ideas for exam"),
    opened_file_path: str = Form(""),
    course_path: str = Form(""),
    include_course_context: bool = Form(True),
    user_id: SignedInUser = None,
):
    if task not in ("flashcards", "mcq"):
        return JSONResponse({"error": "task must be 'flashcards' or 'mcq'."}, status_code=400)
    _limit_study(user_id)
    ctx, cerr = _build_study_scope_context(user_id, opened_file_path, course_path, focus_query, include_course_context)
    if cerr:
        return JSONResponse({"error": cerr}, status_code=400)
    data, err = _run_study_ai(user_id, llm_pipeline.cheap_study_json, ctx, task, max(1, min(count, 30)))
    if err:
        return JSONResponse({"error": err}, status_code=503)
    return JSONResponse(
        {
            "task": task,
            "items": data,
            "grounded_on_path": strip_storage_path(opened_file_path or ""),
            "context_chars": len(ctx or ""),
        }
    )


def _summary(user_id: str, focus_query: str, opened_file_path: str, course_path: str, include_course_context: bool):
    _limit_study(user_id)
    focus = (focus_query or "").strip() or "main concepts"
    ctx, cerr = _build_study_scope_context(user_id, opened_file_path, course_path, focus, include_course_context)
    if cerr:
        return JSONResponse({"error": cerr}, status_code=400)
    summary_text, err = _run_study_ai(user_id, llm_pipeline.topic_summary, ctx)
    if err:
        return JSONResponse({"error": err}, status_code=503)
    return JSONResponse(
        {
            "summary": summary_text,
            "grounded_on_path": strip_storage_path(opened_file_path or ""),
            "context_chars": len(ctx or ""),
        }
    )


@router.post("/study/summary")
def study_summary(
    focus_query: str = Form("main concepts"),
    opened_file_path: str = Form(""),
    course_path: str = Form(""),
    include_course_context: bool = Form(True),
    user_id: SignedInUser = None,
):
    return _summary(user_id, focus_query, opened_file_path, course_path, include_course_context)


@router.post("/study/mindmap", deprecated=True)
def study_mindmap(
    focus_query: str = Form("main concepts"),
    opened_file_path: str = Form(""),
    course_path: str = Form(""),
    include_course_context: bool = Form(True),
    user_id: SignedInUser = None,
):
    """Old name for /study/summary, kept so older frontends keep working."""
    return _summary(user_id, focus_query, opened_file_path, course_path, include_course_context)


# ---------- Saved decks + spaced repetition ----------
@router.post("/study/decks")
def create_deck(name: str = Form(""), source_path: str = Form(""), cards: str = Form(...), user_id: SignedInUser = None):
    rate_limit.check("deck-changes", user_id, *LIMIT_DECK_CHANGES_PER_USER)
    try:
        parsed = json.loads(cards)
    except json.JSONDecodeError:
        return JSONResponse({"error": "cards must be a JSON list."}, status_code=400)
    if not isinstance(parsed, list):
        return JSONResponse({"error": "cards must be a JSON list."}, status_code=400)
    try:
        deck = study_store.create_deck(user_id, name, strip_storage_path(source_path), [c for c in parsed if isinstance(c, dict)])
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(deck, status_code=201)


@router.get("/study/decks")
def list_decks(user_id: SignedInUser = None):
    return {"decks": study_store.list_decks(user_id)}


@router.get("/study/decks/{deck_id}/cards")
def deck_cards(deck_id: str, due_only: bool = False, limit: int = 50, user_id: SignedInUser = None):
    cards = study_store.deck_cards(user_id, deck_id, due_only, limit)
    if cards is None:
        return JSONResponse({"error": "Deck not found."}, status_code=404)
    return {"cards": cards}


@router.post("/study/cards/{card_id}/review")
def review_card(card_id: str, grade: str = Form(...), user_id: SignedInUser = None):
    try:
        result = study_store.review_card(user_id, card_id, (grade or "").strip().lower())
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if result is None:
        return JSONResponse({"error": "Card not found."}, status_code=404)
    usage.record(user_id, reviews=1)
    return result


@router.delete("/study/decks/{deck_id}")
def delete_deck(deck_id: str, user_id: SignedInUser = None):
    rate_limit.check("deck-changes", user_id, *LIMIT_DECK_CHANGES_PER_USER)
    if not study_store.delete_deck(user_id, deck_id):
        return JSONResponse({"error": "Deck not found."}, status_code=404)
    return {"message": "Deck deleted."}


@router.get("/study/decks/{deck_id}/export")
def export_deck(deck_id: str, user_id: SignedInUser = None):
    """CSV (front, back) that Anki can import (File → Import)."""
    exported = study_store.export_csv(user_id, deck_id)
    if exported is None:
        return JSONResponse({"error": "Deck not found."}, status_code=404)
    name, csv_text = exported
    filename = (re.sub(r"[^\w\- ]+", "_", name).strip() or "flashcards") + ".csv"
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------- Today dashboard ----------
def _end_of_today(now: float) -> int:
    tomorrow = usage.today(now) + dt.timedelta(days=1)
    return int(dt.datetime.combine(tomorrow, dt.time(), tzinfo=usage.tz()).timestamp())


def _active(rec: dict) -> bool:
    return any(int(rec.get(k) or 0) for k in ("reviews", "questions", "study_requests"))


@router.get("/study/dashboard")
def dashboard(user_id: SignedInUser = None):
    now = time.time()
    today = usage.today(now)
    by_day = {r.get("day"): r for r in usage.records_since(today - dt.timedelta(days=90), user_id)}

    # Streak: consecutive active days ending today (or yesterday, if today hasn't started yet).
    streak = 0
    day = today if _active(by_day.get(today.isoformat(), {})) else today - dt.timedelta(days=1)
    while _active(by_day.get(day.isoformat(), {})):
        streak += 1
        day -= dt.timedelta(days=1)

    activity = []
    for i in range(13, -1, -1):
        d = (today - dt.timedelta(days=i)).isoformat()
        rec = by_day.get(d, {})
        activity.append({"day": d, "reviews": int(rec.get("reviews") or 0), "questions": int(rec.get("questions") or 0)})

    upcoming = [e for e in study_store.list_exams(user_id, today) if e["days_left"] >= 0]
    exam = None
    if upcoming:
        e = upcoming[0]
        today_tasks = next((p["tasks"] for p in e["plan"] if p["date"] == today.isoformat()), [])
        exam = {k: e[k] for k in ("exam_id", "course", "exam_date", "days_left")} | {"today": today_tasks}

    return {
        "due_today": study_store.due_count(user_id, until=_end_of_today(now)),
        "reviewed_today": int(by_day.get(today.isoformat(), {}).get("reviews") or 0),
        "streak_days": streak,
        "decks": len(study_store.list_decks(user_id)),
        "weak_topics": study_store.weak_topics(user_id),
        "activity": activity,
        "exam": exam,
    }


@router.post("/study/mcq_results")
def mcq_results(path: str = Form(...), correct: int = Form(...), total: int = Form(...), user_id: SignedInUser = None):
    """Record how a quiz went for a file or course folder (feeds "weak topics")."""
    path = strip_storage_path(path)
    if not path or not (0 <= correct <= total <= 100) or total == 0:
        return JSONResponse({"error": "Give a path and 0 ≤ correct ≤ total ≤ 100."}, status_code=400)
    study_store.mcq_record(user_id, path, correct, total)
    return {"message": "Saved."}


# ---------- Exam mode ----------
def _course_files(user_id: str, course_path: str) -> tuple[str, list[str]]:
    course = strip_storage_path(course_path or "")
    paths = file_paths(get_vfs_tree_clean(user_id))
    return course, [p for p in paths if course and (p == course or p.startswith(course + "/"))]


@router.post("/study/exam")
def exam_questions(course_path: str = Form(...), count: int = Form(15), user_id: SignedInUser = None):
    """A mock test: MCQs drawn from every file in a course folder."""
    course, files = _course_files(user_id, course_path)
    if not files:
        return JSONResponse({"error": "That folder has no files to make questions from."}, status_code=400)
    _limit_study(user_id)
    budget = 50000
    share = max(1500, budget // len(files))
    parts, used = [], []
    for p in files:
        if budget <= 0:
            break
        text = _study_text_for_path(user_id, p)
        if not text:
            continue
        piece = text[: min(share, budget)]
        parts.append(f"--- FILE: {p} ---\n{piece}")
        used.append(p)
        budget -= len(piece)
    if not parts:
        return JSONResponse({"error": "No text found in that folder's files yet."}, status_code=400)
    ctx = f"Mock exam for the course folder: {course}\n\n" + "\n\n".join(parts)
    data, err = _run_study_ai(user_id, llm_pipeline.cheap_study_json, ctx, "mcq", max(5, min(count, 30)))
    if err:
        return JSONResponse({"error": err}, status_code=503)
    return {"items": data, "course": course, "files_used": used}


@router.post("/study/exam_plan")
def exam_plan(course_path: str = Form(...), exam_date: str = Form(...), user_id: SignedInUser = None):
    try:
        exam_day = dt.date.fromisoformat((exam_date or "").strip())
    except ValueError:
        return JSONResponse({"error": "Exam date must look like 2026-11-20."}, status_code=400)
    course, files = _course_files(user_id, course_path)
    if not course:
        return JSONResponse({"error": "Pick a course folder."}, status_code=400)
    rate_limit.check("deck-changes", user_id, *LIMIT_DECK_CHANGES_PER_USER)
    try:
        return study_store.save_exam(user_id, course, files, exam_day, usage.today())
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.get("/study/exams")
def list_exams(user_id: SignedInUser = None):
    return {"exams": study_store.list_exams(user_id, usage.today())}


@router.delete("/study/exams/{exam_id}")
def delete_exam(exam_id: str, user_id: SignedInUser = None):
    if not study_store.delete_exam(user_id, exam_id):
        return JSONResponse({"error": "Exam not found."}, status_code=404)
    return {"message": "Exam plan deleted."}
