"""
Saved flashcard decks with spaced repetition, stored per user in `user_{user_id}_study`.

Each deck and each card is one small record. A card's schedule lives in its metadata
(due_at, interval_days, ease, reps, lapses) so "what's due" is a simple metadata filter.
Scheduling is a simplified SM-2 (the algorithm behind Anki's original scheduler).
"""
import csv
import datetime as dt
import io
import json
import time
import uuid

from backend.chroma_store import (
    _delete_collection,
    _dummy_embedding,
    _get_collection_or_none,
    _get_or_create_collection,
    delete_ids,
    get_all,
    short_id,
    write_records,
)

GRADES = ("again", "hard", "good", "easy")
MAX_CARDS_PER_DECK = 200
MAX_FIELD_CHARS = 4000  # keeps each card record well under Chroma Cloud's 16 KB document limit
RELEARN_SECONDS = 600   # "again" shows the card again in 10 minutes
DAY = 86400


def _collection(user_id: str, create: bool):
    name = f"user_{user_id}_study"
    return _get_or_create_collection(name) if create else _get_collection_or_none(name)


def create_deck(user_id: str, name: str, source_path: str, cards: list[dict]) -> dict:
    col = _collection(user_id, create=True)
    now = int(time.time())
    deck_id = uuid.uuid4().hex[:16]
    clean = []
    for c in cards[:MAX_CARDS_PER_DECK]:
        front = str(c.get("front") or "").strip()[:MAX_FIELD_CHARS]
        back = str(c.get("back") or "").strip()[:MAX_FIELD_CHARS]
        if front and back:
            clean.append({"front": front, "back": back, "source": str(c.get("source") or "notes")[:40]})
    if not clean:
        raise ValueError("A deck needs at least one card with a front and a back.")
    name = (name or "").strip()[:120] or "Flashcards"
    col.add(
        ids=[f"deck_{deck_id}"],
        documents=[""],
        embeddings=[_dummy_embedding()],
        metadatas=[{
            "kind": "deck",
            "deck_id": deck_id,
            "name": name,
            "source_path": (source_path or "")[:1000],
            "created_at": now,
            "card_count": len(clean),
        }],
    )
    card_ids = [f"card_{uuid.uuid4().hex[:20]}" for _ in clean]
    write_records(
        col,
        "add",
        card_ids,
        documents=[json.dumps(c, ensure_ascii=False) for c in clean],
        embeddings=[_dummy_embedding()] * len(clean),
        metadatas=[
            {"kind": "card", "deck_id": deck_id, "due_at": now, "interval_days": 0.0, "ease": 2.5, "reps": 0, "lapses": 0}
            for _ in clean
        ],
    )
    return {"deck_id": deck_id, "name": name, "card_count": len(clean)}


def list_decks(user_id: str) -> list[dict]:
    col = _collection(user_id, create=False)
    if col is None:
        return []
    now = int(time.time())
    decks = get_all(col, where={"kind": "deck"}, include=["metadatas"])
    due = get_all(col, where={"$and": [{"kind": "card"}, {"due_at": {"$lte": now}}]}, include=["metadatas"])
    due_counts: dict[str, int] = {}
    for m in due.get("metadatas") or []:
        did = (m or {}).get("deck_id")
        due_counts[did] = due_counts.get(did, 0) + 1
    out = []
    for m in decks.get("metadatas") or []:
        m = m or {}
        out.append({
            "deck_id": m.get("deck_id"),
            "name": m.get("name") or "Flashcards",
            "source_path": m.get("source_path") or "",
            "card_count": int(m.get("card_count") or 0),
            "due_count": due_counts.get(m.get("deck_id"), 0),
            "created_at": int(m.get("created_at") or 0),
        })
    out.sort(key=lambda d: d["created_at"], reverse=True)
    return out


def _deck_exists(col, deck_id: str) -> bool:
    return bool(col.get(ids=[f"deck_{deck_id}"], include=[]).get("ids"))


def deck_cards(user_id: str, deck_id: str, due_only: bool, limit: int) -> list[dict] | None:
    """Cards of a deck (None if the deck doesn't exist). due_only: cards due now, soonest first."""
    col = _collection(user_id, create=False)
    if col is None or not _deck_exists(col, deck_id):
        return None
    where: dict = {"$and": [{"kind": "card"}, {"deck_id": deck_id}]}
    if due_only:
        where["$and"].append({"due_at": {"$lte": int(time.time())}})
    res = get_all(col, where=where, include=["documents", "metadatas"])
    cards = []
    for cid, doc, meta in zip(res["ids"], res.get("documents") or [], res.get("metadatas") or []):
        body = json.loads(doc or "{}")
        meta = meta or {}
        cards.append({
            "card_id": cid,
            "front": body.get("front", ""),
            "back": body.get("back", ""),
            "source": body.get("source", "notes"),
            "due_at": int(meta.get("due_at") or 0),
            "interval_days": float(meta.get("interval_days") or 0),
            "reps": int(meta.get("reps") or 0),
        })
    cards.sort(key=lambda c: c["due_at"])
    return cards[: max(1, min(limit, MAX_CARDS_PER_DECK))]


def schedule(meta: dict, grade: str, now: int) -> dict:
    """Next review for a card after the student grades it (simplified SM-2)."""
    ease = float(meta.get("ease") or 2.5)
    interval = float(meta.get("interval_days") or 0)
    reps = int(meta.get("reps") or 0)
    lapses = int(meta.get("lapses") or 0)
    if grade == "again":
        return {**meta, "ease": max(1.3, ease - 0.2), "interval_days": 0.0, "reps": 0,
                "lapses": lapses + 1, "due_at": now + RELEARN_SECONDS}
    if grade == "hard":
        ease = max(1.3, ease - 0.15)
        interval = 1.0 if reps == 0 else max(1.0, interval * 1.2)
    elif grade == "good":
        interval = 1.0 if reps == 0 else (3.0 if reps == 1 else interval * ease)
    else:  # easy
        ease += 0.15
        interval = 3.0 if reps == 0 else max(4.0, interval * ease * 1.3)
    interval = round(min(interval, 365.0), 2)
    return {**meta, "ease": round(ease, 3), "interval_days": interval, "reps": reps + 1,
            "lapses": lapses, "due_at": now + int(interval * DAY)}


def review_card(user_id: str, card_id: str, grade: str) -> dict | None:
    if grade not in GRADES:
        raise ValueError("Grade must be one of: again, hard, good, easy.")
    col = _collection(user_id, create=False)
    if col is None or not card_id.startswith("card_"):
        return None
    res = col.get(ids=[card_id], include=["metadatas"])
    if not res.get("ids"):
        return None
    new_meta = schedule(res["metadatas"][0] or {}, grade, int(time.time()))
    col.update(ids=[card_id], metadatas=[new_meta])
    return {"card_id": card_id, "due_at": new_meta["due_at"], "interval_days": new_meta["interval_days"]}


def delete_deck(user_id: str, deck_id: str) -> bool:
    col = _collection(user_id, create=False)
    if col is None or not _deck_exists(col, deck_id):
        return False
    delete_ids(col, get_all(col, where={"deck_id": deck_id}, include=[])["ids"])
    col.delete(ids=[f"deck_{deck_id}"])
    return True


def export_csv(user_id: str, deck_id: str) -> tuple[str, str] | None:
    """(deck name, CSV text with front,back rows) for importing into Anki, or None."""
    col = _collection(user_id, create=False)
    if col is None:
        return None
    deck = col.get(ids=[f"deck_{deck_id}"], include=["metadatas"])
    if not deck.get("ids"):
        return None
    cards = deck_cards(user_id, deck_id, due_only=False, limit=MAX_CARDS_PER_DECK) or []
    buf = io.StringIO()
    writer = csv.writer(buf)
    for c in cards:
        writer.writerow([c["front"], c["back"]])
    return (deck["metadatas"][0] or {}).get("name") or "flashcards", buf.getvalue()


def delete_all(user_id: str) -> None:
    _delete_collection(f"user_{user_id}_study")


def due_count(user_id: str, until: int | None = None) -> int:
    """Number of cards due by `until` (epoch seconds; default now) across all decks."""
    col = _collection(user_id, create=False)
    if col is None:
        return 0
    until = int(until if until is not None else time.time())
    return len(get_all(col, where={"$and": [{"kind": "card"}, {"due_at": {"$lte": until}}]}, include=[])["ids"])


# ---------- Quiz results per file (for "weak topics") ----------
def mcq_record(user_id: str, path: str, correct: int, total: int) -> dict:
    col = _collection(user_id, create=True)
    rid = short_id("mcq_", path)
    res = col.get(ids=[rid], include=["metadatas"])
    meta = dict((res.get("metadatas") or [{}])[0] or {}) if res.get("ids") else {"kind": "mcqstat", "path": path[:1000]}
    meta["correct"] = int(meta.get("correct") or 0) + int(correct)
    meta["total"] = int(meta.get("total") or 0) + int(total)
    meta["updated_at"] = int(time.time())
    col.upsert(ids=[rid], documents=[""], embeddings=[_dummy_embedding()], metadatas=[meta])
    return meta


def weak_topics(user_id: str, min_answered: int = 3, below: float = 0.7, limit: int = 5) -> list[dict]:
    """Files whose quiz accuracy is lowest (only files with at least `min_answered` answers)."""
    col = _collection(user_id, create=False)
    if col is None:
        return []
    out = []
    for m in get_all(col, where={"kind": "mcqstat"}, include=["metadatas"]).get("metadatas") or []:
        total, correct = int((m or {}).get("total") or 0), int((m or {}).get("correct") or 0)
        if total >= min_answered and correct / total < below:
            out.append({"path": m.get("path") or "", "correct": correct, "total": total, "accuracy": round(correct / total, 3)})
    out.sort(key=lambda t: (t["accuracy"], -t["total"]))
    return out[:limit]


# ---------- Exam plans ----------
def build_exam_plan(files: list[str], exam_day: dt.date, today: dt.date) -> list[dict]:
    """
    A day-by-day plan from today to the exam: spread the course files over the revision days
    (a second pass if there's time), a timed mock test the day before, light review on exam day.
    """
    days_left = (exam_day - today).days
    if days_left < 0:
        raise ValueError("The exam date is in the past.")
    if days_left > 180:
        raise ValueError("Pick an exam date within the next 6 months.")
    names = [f.rsplit("/", 1)[-1] for f in files]
    if days_left == 0:
        return [{"date": today.isoformat(), "tasks": ["Exam today: review your due flashcards and skim your main-source notes. Good luck!"]}]

    study_days = [today + dt.timedelta(days=d) for d in range(days_left)]
    mock_day = study_days[-1] if days_left >= 2 else None
    revision_days = study_days[:-1] if mock_day else study_days
    n = len(revision_days)
    groups: list[list[str]] = [[] for _ in range(n)]
    if names:
        if len(names) >= n:
            for i, name in enumerate(names):
                groups[i * n // len(names)].append(name)
        else:
            for i in range(n):
                groups[i].append(names[i % len(names)])

    plan = []
    for i, day in enumerate(revision_days):
        tasks = []
        if groups[i]:
            again = names and len(names) < n and i >= len(names)
            tasks.append(("Revise again: " if again else "Revise: ") + ", ".join(groups[i]) + " (ask about anything unclear)")
        tasks.append("Make flashcards for what you revised, and review the cards due today")
        if i and i % 3 == 0:
            tasks.append("Practice quiz (Mock MCQ) on the files from the last few days")
        plan.append({"date": day.isoformat(), "tasks": tasks})
    if mock_day:
        plan.append({
            "date": mock_day.isoformat(),
            "tasks": ["Timed mock test for the whole course (Exam tab)", "Go over the questions you got wrong and re-read those files"],
        })
    plan.append({"date": exam_day.isoformat(), "tasks": ["Exam day: review only your due flashcards. Good luck!"]})
    return plan


def _exam_public(meta: dict, plan: list[dict], today: dt.date) -> dict:
    exam_day = dt.date.fromisoformat(meta["exam_date"])
    return {
        "exam_id": meta["exam_id"],
        "course": meta.get("course") or "",
        "exam_date": meta["exam_date"],
        "days_left": (exam_day - today).days,
        "plan": plan,
    }


def save_exam(user_id: str, course: str, files: list[str], exam_day: dt.date, today: dt.date) -> dict:
    plan = build_exam_plan(files, exam_day, today)
    col = _collection(user_id, create=True)
    exam_id = uuid.uuid4().hex[:16]
    meta = {
        "kind": "exam",
        "exam_id": exam_id,
        "course": course[:500],
        "exam_date": exam_day.isoformat(),
        "exam_day_num": exam_day.year * 10000 + exam_day.month * 100 + exam_day.day,
        "created_at": int(time.time()),
    }
    doc = json.dumps(plan, ensure_ascii=False)
    if len(doc.encode("utf-8")) > 15000:  # keep within one Chroma Cloud document
        raise ValueError("That plan is too long to save. Pick a closer exam date or a smaller folder.")
    col.add(ids=[f"exam_{exam_id}"], documents=[doc], embeddings=[_dummy_embedding()], metadatas=[meta])
    return _exam_public(meta, plan, today)


def list_exams(user_id: str, today: dt.date) -> list[dict]:
    col = _collection(user_id, create=False)
    if col is None:
        return []
    res = get_all(col, where={"kind": "exam"}, include=["documents", "metadatas"])
    exams = [_exam_public(m or {}, json.loads(d or "[]"), today) for d, m in zip(res.get("documents") or [], res.get("metadatas") or [])]
    exams.sort(key=lambda e: e["exam_date"])
    return exams


def delete_exam(user_id: str, exam_id: str) -> bool:
    col = _collection(user_id, create=False)
    if col is None or not col.get(ids=[f"exam_{exam_id}"], include=[]).get("ids"):
        return False
    col.delete(ids=[f"exam_{exam_id}"])
    return True
