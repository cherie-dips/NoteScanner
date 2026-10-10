"""
Finding the note excerpts that answer a question.

Search order: chat uploads (+ button) → the open file → other files in the same course folder →
all other notes. When the question is about a shared course PDF (Interview.ai's Notes tab sends its
path), that PDF and then the rest of its course come first. Every stage is scored as cosine
similarity (0..1), so stages can be compared.
"""
from dataclasses import dataclass, field

import numpy as np

from backend import library
from backend.chroma_store import collection_distance_space, distance_to_similarity, get_user_collection
from backend.ingest_api import embed_texts
from backend.settings import (
    MAIN_SOURCE_BOOST,
    MIN_SCORE_ALL_NOTES,
    MIN_SCORE_CHAT_UPLOAD,
    MIN_SCORE_COURSE,
    MIN_SCORE_LIBRARY_FILE,
    MIN_SCORE_LIBRARY_SUBJECT,
    MIN_SCORE_OPEN_FILE,
    SOURCE_SCORE_WINDOW,
)

MAX_SELECTED = 8
OPEN_LIBRARY_FILE_LEAD = 0.10  # ranking lead for the course PDF the student has open
OPEN_PDF_KEEP = 3              # passages of the open PDF always sent when it is nearly as relevant...
OPEN_PDF_KEEP_GAP = 0.25       # ...as the best passage of its course (within this much)


@dataclass
class SearchResult:
    stage: str = ""
    selected: list[dict] = field(default_factory=list)
    best_scores: dict[str, float] = field(default_factory=dict)

    def context(self) -> str:
        parts = []
        for d in self.selected:
            content = (d.get("content") or "").strip()
            if not content:
                continue
            md = d.get("metadata") or {}
            label = md.get("path") or "chunk"
            if md.get("library"):
                label = f"Course notes: {md.get('label') or label}"
            # Most course PDFs are one long page: a page number would only add noise there.
            if md.get("page") and int(md.get("pages") or 2) > 1:
                label += f", page {md['page']}"
            if md.get("is_primary_authority"):
                label += " (main source)"
            parts.append(f"[{label}]\n{content[:1200]}")
        return "\n\n".join(parts)

    def source_documents(self) -> list[dict]:
        return [{k: v for k, v in d.items() if k != "_score"} for d in self.selected]


def _search_chunks(
    col, space: str, q_emb: list[float], where: dict | None, allowed: set[str], n_results: int
) -> list[dict]:
    """
    Best stored chunks matching `where` (None = whole collection), kept only if their file is still
    in the folder tree (`allowed`), scored as cosine similarity with a small boost for main-source files.
    """
    res = col.query(
        query_embeddings=[q_emb],
        n_results=n_results,
        include=["documents", "metadatas", "distances"],
        **({"where": where} if where else {}),
    )
    ids = (res.get("ids") or [[]])[0] or []
    docs = (res.get("documents") or [[]])[0] or []
    metas = (res.get("metadatas") or [[]])[0] or []
    dists = (res.get("distances") or [[]])[0] or []
    out = []
    for i, cid in enumerate(ids):
        content = (docs[i] if i < len(docs) else "") or ""
        md = metas[i] if i < len(metas) and isinstance(metas[i], dict) else {}
        if not content.strip() or md.get("path") not in allowed:
            continue
        dist = float(dists[i]) if i < len(dists) and dists[i] is not None else 2.0
        score = distance_to_similarity(dist, space)
        if md.get("is_primary_authority"):
            score += MAIN_SOURCE_BOOST
        out.append({"id": cid, "content": content, "metadata": md, "_score": score})
    out.sort(key=lambda d: d["_score"], reverse=True)
    return out


def _search_chat_uploads(ephemeral: list[dict], q_vec: np.ndarray) -> list[dict]:
    """Top 3 chunks per chat-uploaded file, scored as cosine similarity."""
    out: list[dict] = []
    for i, it in enumerate(ephemeral):
        chunks = it.get("chunks") or []
        embs = it.get("embeddings")
        if not chunks or embs is None or len(embs) != len(chunks):
            continue
        scores = embs @ q_vec
        for j, idx in enumerate(np.argsort(-scores)[:3]):
            out.append({
                "id": f"ephemeral|{i}|{j}",
                "content": chunks[int(idx)],
                "metadata": {
                    "path": f"[chat-upload] {it.get('name') or 'file'}",
                    "source_file": it.get("name") or "chat-upload",
                    "ephemeral_chat_upload": 1,
                },
                "_score": float(scores[int(idx)]),
            })
    out.sort(key=lambda d: d["_score"], reverse=True)
    return out


def _closest(docs: list[dict]) -> list[dict]:
    """The best excerpts of a stage: those scoring close to its top match, at most MAX_SELECTED."""
    if not docs:
        return []
    floor = docs[0]["_score"] - SOURCE_SCORE_WINDOW
    return [d for d in docs if d["_score"] >= floor][:MAX_SELECTED]


def course_of(opened: str, course_path: str) -> str:
    course = course_path or ""
    if not course and opened:
        course = opened.split("/", 1)[0] if "/" in opened else opened
    return course.split("/", 1)[0] if course else ""


def find_context(
    user_id: str,
    question: str,
    all_paths: list[str],
    opened: str,
    course: str,
    ephemeral: list[dict],
    include_other_notes: bool = True,
    library_path: str = "",
) -> SearchResult:
    col = get_user_collection(user_id)
    space = collection_distance_space(col)
    q_vec = np.asarray(embed_texts([question])[0], dtype=np.float32)
    q_emb = q_vec.tolist()
    all_set = set(all_paths)

    eph_docs = _search_chat_uploads(ephemeral, q_vec)
    open_docs = _search_chunks(col, space, q_emb, {"path": opened}, all_set, 6) if opened in all_set else []

    # Shared course library: the open course PDF and the other PDFs of the same course, ranked
    # together with a small lead for the open PDF, so a lecture that clearly answers the question
    # wins over a loosely related open PDF (course notes on one subject all score fairly high).
    lib_file_docs: list[dict] = []
    lib_subject_docs: list[dict] = []
    lib_file, lib_subject = library.scope_of(library_path)
    if lib_file:
        lib_file_docs = library.search(q_emb, {"path": lib_file}, 6)
    if lib_subject and (include_other_notes or not lib_file):
        subject_where = {"subject": lib_subject}
        if lib_file:
            subject_where = {"$and": [subject_where, {"path": {"$ne": lib_file}}]}
        lib_subject_docs = library.search(q_emb, subject_where, 24)[:MAX_SELECTED]
    lib_docs = sorted(
        [{**d, "_score": d["_score"] + OPEN_LIBRARY_FILE_LEAD} for d in lib_file_docs] + lib_subject_docs,
        key=lambda d: d["_score"],
        reverse=True,
    )
    from_open_pdf = bool(lib_docs) and lib_docs[0]["metadata"].get("path") == lib_file
    lib_stage = "library_file" if from_open_pdf else "library_subject"
    lib_min = MIN_SCORE_LIBRARY_FILE if from_open_pdf else MIN_SCORE_LIBRARY_SUBJECT

    # Course and "everything else" are selected with metadata filters (few predicates, no huge
    # path lists), then checked against the tree so deleted files never show up.
    course_docs: list[dict] = []
    other_docs: list[dict] = []
    if include_other_notes and all_paths:
        not_opened = {"path": {"$ne": opened}} if opened else None
        if course:
            course_where = {"$and": [{"course_id": course}, not_opened]} if not_opened else {"course_id": course}
            course_docs = _search_chunks(col, space, q_emb, course_where, all_set, 24)[:MAX_SELECTED]
            other_where = {"course_id": {"$ne": course}}
        else:
            other_where = not_opened
        other_docs = _search_chunks(col, space, q_emb, other_where, all_set, 24)[:MAX_SELECTED]

    stages = [
        ("ephemeral", eph_docs, MIN_SCORE_CHAT_UPLOAD),
        (lib_stage, lib_docs, lib_min),
        ("opened", open_docs, MIN_SCORE_OPEN_FILE),
        ("course", course_docs, MIN_SCORE_COURSE),
        ("all_notes", other_docs, MIN_SCORE_ALL_NOTES),
    ]
    scored = [*stages, ("library_file", lib_file_docs, 0), ("library_subject", lib_subject_docs, 0)]
    result = SearchResult(
        best_scores={name: (round(docs[0]["_score"], 4) if docs else -1.0) for name, docs, _ in scored}
    )
    # Use the highest-priority stage that is relevant enough; otherwise the best-scoring one.
    for name, docs, min_score in stages:
        if docs and docs[0]["_score"] >= min_score:
            result.stage, result.selected = name, _closest(docs)
            _keep_open_pdf(result, lib_file_docs, lib_subject_docs)
            return result
    # A question about a course PDF never falls back to loosely related notes of the student's own
    # (e.g. sample physics notes for a maths course): only the course library itself.
    fallback = [(lib_stage, lib_docs, lib_min)] if lib_subject else stages
    available = [(name, docs) for name, docs, _ in fallback if docs]
    if available:
        result.stage, docs = max(available, key=lambda s: s[1][0]["_score"])
        result.selected = _closest(docs)
        _keep_open_pdf(result, lib_file_docs, lib_subject_docs)
    return result


def _keep_open_pdf(result: SearchResult, file_docs: list[dict], subject_docs: list[dict]) -> None:
    """
    The student asked while reading a course PDF: make sure its best passages reach the answer when
    it is nearly as relevant as the rest of the course, even if many other passages score a little
    higher (e.g. exam answers that all mention "time complexity"). They go first in the context.
    """
    if not file_docs or not result.stage.startswith("library"):
        return
    best_other = subject_docs[0]["_score"] if subject_docs else -1.0
    if file_docs[0]["_score"] < max(MIN_SCORE_LIBRARY_FILE, best_other - OPEN_PDF_KEEP_GAP):
        return
    keep = [d for d in file_docs[:OPEN_PDF_KEEP] if d["_score"] >= MIN_SCORE_LIBRARY_FILE]
    kept_ids = {d["id"] for d in keep}
    result.selected = (keep + [d for d in result.selected if d["id"] not in kept_ids])[:MAX_SELECTED]

