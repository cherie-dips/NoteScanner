"""Answer ratings and general feedback from students, stored in the `feedback` collection."""
import json
import time
import uuid

from backend.chroma_store import _dummy_embedding, _get_or_create_collection, delete_ids, get_all

KINDS = ("answer", "general")
RATINGS = ("up", "down", "")
_FIELD_LIMITS = {"comment": 2000, "question": 1000, "answer": 2000, "sources": 1000}


def _collection():
    return _get_or_create_collection("feedback")


def add(user_id: str, kind: str, rating: str, selection_stage: str = "", **fields: str) -> None:
    doc = {k: str(fields.get(k) or "")[:limit] for k, limit in _FIELD_LIMITS.items()}  # stays well under 16 KB
    _collection().add(
        ids=[f"fb_{uuid.uuid4().hex}"],
        documents=[json.dumps(doc, ensure_ascii=False)],
        embeddings=[_dummy_embedding()],
        metadatas=[{
            "user_id": user_id,
            "kind": kind,
            "rating": rating,
            "selection_stage": (selection_stage or "")[:40],
            "created_at": int(time.time()),
        }],
    )


def recent(limit: int = 50, since: int = 0) -> list[dict]:
    res = get_all(_collection(), where={"created_at": {"$gte": int(since)}}, include=["documents", "metadatas"])
    rows = []
    for doc, meta in zip(res.get("documents") or [], res.get("metadatas") or []):
        meta = meta or {}
        rows.append({**json.loads(doc or "{}"), **{k: meta.get(k) for k in ("user_id", "kind", "rating", "selection_stage", "created_at")}})
    rows.sort(key=lambda r: r.get("created_at") or 0, reverse=True)
    return rows[:limit]


def delete_user(user_id: str) -> None:
    col = _collection()
    delete_ids(col, get_all(col, where={"user_id": user_id}, include=[])["ids"])
