"""
The shared course library: course PDFs read once on the server and searched by every student.

The PDFs come from a public Supabase Storage bucket (the Notes tab of SDE-Prep; see LIBRARY_* in
settings.py). They live in two shared collections, so nothing is copied per student:

- library_notes      search passages + embeddings; metadata: path, subject, page and `y` (where on
                     the page the passage starts, 0 = top, 1 = bottom) so a viewer can scroll to it
- library_documents  one record per PDF (eTag, status, page counts) plus its full text in parts,
                     which the study tools read

sync() brings the index in line with the bucket: new or changed PDFs (by eTag) are read and
indexed, removed ones are deleted. Pages that contain real text are read with PyMuPDF. Handwritten
pages (pen strokes) and scans are cut into page-shaped pieces at blank rows, so no line of writing
is split, and read with Sarvam Vision (at most 10 pieces per job, Sarvam's limit) or with Tesseract
when no Sarvam key is set. Students never start a sync: it runs from scripts/index_library.py, the
admin endpoint, or every LIBRARY_SYNC_HOURS.
"""
import bisect
import io
import logging
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import quote

import httpx
import numpy as np
from fastapi import HTTPException

from backend import llm_pipeline, settings, usage
from backend.chroma_store import (
    _get_collection_or_none,
    _get_or_create_collection,
    _read_parts,
    _write_parts,
    collection_distance_space,
    delete_ids,
    distance_to_similarity,
    get_all,
    short_id,
)

logger = logging.getLogger(__name__)

NOTES_COLLECTION = "library_notes"
DOCS_COLLECTION = "library_documents"
USAGE_ID = "library"   # usage counters for pages the library sent to Sarvam (cost on the admin page)
OCR_BATCH = 10         # Sarvam Vision reads at most 10 pages per job
MIN_FILE_BYTES = 1000  # smaller "PDFs" in the bucket are empty placeholders
MAX_FILE_BYTES = 200 * 1024 * 1024
TILE_RATIO = 1.35      # height / width of one piece sent to OCR: about a portrait page
OCR_WIDTH_PX = 1600    # render width for OCR: handwriting stays legible, images stay small
STATUS_CACHE_SECONDS = 60
SARVAM_RETRY_DELAYS = (5, 15)  # seconds before retrying a failed Sarvam job (e.g. an upload timeout)
TESSERACT_FALLBACK = "sarvam_failed_used_tesseract"
NOT_READY = "These course notes aren't ready for AI yet. Please try again later."

FOLDER_NAMES = {
    "class-notes": "Class Notes",
    "tutorials-assignments": "Tutorials/Assignments",
    "exam-practice": "Exam Practice",
    "past-papers": "Past Papers",
}


class BudgetReached(Exception):
    """This month's AI budget is used up; files that need Sarvam wait for the next sync."""


# ---------- Paths ----------
def _clean(path: str) -> str:
    p = (path or "").replace("\\", "/").strip().strip("/")
    return "" if ".." in p.split("/") else p


def _allowed(path: str) -> bool:
    return any(path == pre or path.startswith(pre + "/") for pre in settings.LIBRARY_PREFIXES)


def subject_of(path: str) -> str:
    """'category/subject' of a library path, e.g. plaksha-university/discrete-maths."""
    parts = _clean(path).split("/")
    return "/".join(parts[:2]) if len(parts) >= 2 else ""


def scope_of(library_path: str) -> tuple[str, str]:
    """
    (PDF path or "", subject) for what a client sends: the storage path of the PDF that is open,
    or just a subject prefix. ("", "") when the path is outside the library.
    """
    p = _clean(library_path)
    if not p or not _allowed(p):
        return "", ""
    return (p if p.lower().endswith(".pdf") else ""), subject_of(p)


def _title(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").strip().title()


def subject_label(subject: str) -> str:
    parts = subject.split("/")
    return _title(parts[1]) if len(parts) > 1 else _title(subject)


def label_of(path: str) -> str:
    """Readable name for answers, e.g. 'Discrete Maths › Class Notes › L1'."""
    parts = _clean(path).split("/")
    subject = _title(parts[1]) if len(parts) > 2 else ""
    folder = FOLDER_NAMES.get(parts[2], _title(parts[2])) if len(parts) > 3 else ""
    name = parts[-1].rsplit(".", 1)[0] if parts else ""
    return " › ".join(x for x in (subject, folder, name) if x)


def _doc_id(path: str) -> str:
    return short_id("libdoc", path)


def _chunk_id_base(path: str) -> str:
    return short_id("lib", path)


# ---------- Collections ----------
def _notes(create: bool):
    return _get_or_create_collection(NOTES_COLLECTION) if create else _get_collection_or_none(NOTES_COLLECTION)


def _docs(create: bool):
    return _get_or_create_collection(DOCS_COLLECTION) if create else _get_collection_or_none(DOCS_COLLECTION)


def indexed_files() -> dict[str, dict]:
    """{path: details} for every PDF the library knows about (any status)."""
    col = _docs(create=False)
    if col is None:
        return {}
    res = get_all(col, where={"kind": "library_file"}, include=["metadatas"])
    return {m["path"]: m for m in res.get("metadatas") or [] if m and m.get("path")}


def document_text(path: str) -> str:
    """Full text read from a library PDF ("" if it isn't indexed)."""
    col = _docs(create=False)
    if col is None:
        return ""
    try:
        return (_read_parts(col, _doc_id(path)) or "").strip()
    except Exception:
        logger.warning("Could not read library text for %s", path, exc_info=True)
        return ""


# ---------- The bucket ----------
def _storage_headers() -> dict:
    key = settings.LIBRARY_SUPABASE_KEY
    return {"apikey": key, "Authorization": f"Bearer {key}"}


def list_source_files() -> list[dict]:
    """Every PDF under LIBRARY_PREFIXES in the bucket: [{path, etag, size}], placeholders left out."""
    url = f"{settings.LIBRARY_SUPABASE_URL}/storage/v1/object/list/{settings.LIBRARY_BUCKET}"
    out: list[dict] = []

    def walk(prefix: str) -> None:
        offset = 0
        while True:
            r = httpx.post(
                url,
                headers=_storage_headers(),
                json={"prefix": prefix, "limit": 100, "offset": offset, "sortBy": {"column": "name", "order": "asc"}},
                timeout=30,
            )
            r.raise_for_status()
            items = r.json() or []
            for it in items:
                name = it.get("name") or ""
                if not name or name.startswith(".emptyFolderPlaceholder"):
                    continue
                path = f"{prefix}/{name}"
                if it.get("id") is None:  # a folder
                    walk(path)
                    continue
                meta = it.get("metadata") or {}
                size = int(meta.get("size") or 0)
                if name.lower().endswith(".pdf") and MIN_FILE_BYTES <= size <= MAX_FILE_BYTES:
                    etag = str(meta.get("eTag") or it.get("updated_at") or "").strip('"')
                    out.append({"path": path, "etag": etag, "size": size})
            if len(items) < 100:
                return
            offset += 100

    for prefix in settings.LIBRARY_PREFIXES:
        walk(prefix)
    return out


def download(path: str) -> bytes:
    url = (
        f"{settings.LIBRARY_SUPABASE_URL}/storage/v1/object/public/{settings.LIBRARY_BUCKET}/"
        + "/".join(quote(seg) for seg in path.split("/"))
    )
    r = httpx.get(url, timeout=300, follow_redirects=True)
    r.raise_for_status()
    return r.content


# ---------- Reading a PDF ----------
@dataclass
class Tile:
    """A page-shaped piece of a page, rendered for OCR."""
    page: int     # 1-based page number
    y0: float     # where the piece starts on the page (0..1)
    image: bytes  # PNG


@dataclass
class PdfText:
    text: str = ""
    # Where each piece starts in `text`: (offset, page, y0, y1) - it covers y0..y1 of that page.
    anchors: list[tuple[int, int, float, float]] = field(default_factory=list)
    pages: int = 0
    ocr_pieces: int = 0     # pieces that needed OCR
    sarvam_pieces: int = 0  # pieces Sarvam read (paid)
    method: str = ""        # text | ocr | mixed
    notes: list[str] = field(default_factory=list)


def _ocr_mode() -> str:
    mode = settings.LIBRARY_OCR
    if mode == "auto":
        return "sarvam" if llm_pipeline._sarvam_api_key() else "tesseract"
    return mode if mode in ("sarvam", "tesseract", "off") else "tesseract"


def _needs_ocr(page, text: str) -> bool:
    """Handwriting (many pen strokes) or a scan (a large image) needs OCR; typed pages are read as text."""
    chars = len(text)
    strokes = len(page.get_drawings())
    if strokes >= 150:
        return True  # mostly handwriting (OneNote ink exports have hundreds of strokes per page)
    if chars >= 300:
        return False  # real text with a few diagrams
    area = max(page.rect.width * page.rect.height, 1.0)
    for info in page.get_image_info():
        x0, y0, x1, y1 = info.get("bbox") or (0, 0, 0, 0)
        if (x1 - x0) * (y1 - y0) >= 0.3 * area:
            return True  # a scanned page or a photo of one
    return strokes >= 20


def _tile_count(page) -> int:
    w, h = page.rect.width, page.rect.height
    return 1 if h <= w * 1.6 else max(2, round(h / (w * TILE_RATIO)))


def _cut_rows(page, n: int) -> list[float]:
    """n-1 cut positions (points), each moved to the emptiest row near an even split."""
    import fitz  # PyMuPDF

    w, h = page.rect.width, page.rect.height
    scale = min(1.0, 400 / max(w, 1.0))
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csGRAY, alpha=False)
    gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)[:, : pix.width]
    ink = (gray < 200).sum(axis=1)  # dark pixels per row: writing, not the light page background
    step = h / n
    cuts: list[float] = []
    for k in range(1, n):
        target = k * step
        lo = max(int((target - 0.15 * step) * scale), 0)
        hi = min(int((target + 0.15 * step) * scale), len(ink) - 1)
        if hi <= lo:
            cuts.append(target)
            continue
        window = ink[lo : hi + 1]
        candidates = np.flatnonzero(window == window.min()) + lo
        best = int(candidates[np.argmin(np.abs(candidates - target * scale))])
        cuts.append(best / scale)
    return cuts


def _tiles(page) -> list[Tile]:
    import fitz  # PyMuPDF

    w, h = page.rect.width, page.rect.height
    n = _tile_count(page)
    ys = [0.0, *(_cut_rows(page, n) if n > 1 else []), h]
    zoom = min(OCR_WIDTH_PX / max(w, 1.0), 3.0)
    tiles = []
    for y0, y1 in zip(ys, ys[1:]):
        if y1 - y0 < 8:
            continue
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=fitz.Rect(0, y0, w, y1), alpha=False)
        tiles.append(Tile(page=page.number + 1, y0=y0 / h, image=pix.tobytes("png")))
    return tiles


def _tesseract(tile: Tile) -> str:
    import pytesseract
    from PIL import Image

    return pytesseract.image_to_string(Image.open(io.BytesIO(tile.image))).strip()


def _split_by_pages(text: str, page_texts: list[str], n: int) -> list[str]:
    """Split one OCR job's text back into its n pieces (all of it to the first when that fails)."""
    from backend.pages import anchor_page_starts

    out = [""] * n
    starts = anchor_page_starts(text, page_texts) if n > 1 else None
    if not starts:
        out[0] = text
        return out
    bounds = [*starts, (len(text), 0)]
    for (start, number), (end, _) in zip(bounds, bounds[1:]):
        i = min(max(number, 1), n) - 1
        out[i] = (out[i] + "\n\n" + text[start:end]).strip()
    return out


def _sarvam_batch(tiles: list[Tile]) -> list[str] | None:
    """Text of each piece from one Sarvam Vision job (≤ 10 pieces), or None if the job failed."""
    import fitz  # PyMuPDF

    doc = fitz.open()
    try:
        for t in tiles:
            with fitz.open(stream=t.image, filetype="png") as img:
                rect = img[0].rect
            page = doc.new_page(width=rect.width, height=rect.height)
            page.insert_image(page.rect, stream=t.image)
        pdf = doc.tobytes()
    finally:
        doc.close()
    text, err, page_texts = llm_pipeline.transcribe_pdf_with_pages(pdf)
    if err or not (text or "").strip():
        logger.warning("Sarvam Vision failed for a library batch: %s", err or "empty output")
        return None
    return _split_by_pages(text.strip(), page_texts, len(tiles))


def _check_budget() -> None:
    try:
        usage.check_budget()
    except HTTPException as e:
        raise BudgetReached(str(e.detail)) from e


def _ocr(tiles: list[Tile], mode: str, result: PdfText) -> list[str]:
    if mode != "sarvam":
        return [_tesseract(t) for t in tiles]
    texts: list[str] = []
    for i in range(0, len(tiles), OCR_BATCH):
        batch = tiles[i : i + OCR_BATCH]
        _check_budget()
        got = _sarvam_batch(batch)
        for delay in SARVAM_RETRY_DELAYS:  # failures are mostly network hiccups: try again first
            if got is not None:
                break
            time.sleep(delay)
            got = _sarvam_batch(batch)
        if got is None:
            result.notes.append(TESSERACT_FALLBACK)
            got = [_tesseract(t) for t in batch]
        else:
            result.sarvam_pieces += len(batch)
        texts.extend(got)
    return texts


def read_pdf(data: bytes, ocr: str = "tesseract", estimate_only: bool = False) -> PdfText:
    """
    The text of a PDF in reading order, with an anchor (offset, page, y) wherever a piece starts.
    estimate_only counts the pieces that would need OCR without reading them (for --dry-run).
    """
    import fitz  # PyMuPDF

    result = PdfText()
    pieces: list[tuple[int, float, str]] = []  # (page, y, text)
    ocr_pages: dict[int, str] = {}  # page → PyMuPDF's text for it, used only if OCR reads nothing
    pending: list[Tile] = []

    def read_pending(everything: bool) -> None:
        # Full jobs of OCR_BATCH pieces across pages; the rest waits for the next page.
        n = len(pending) if everything else len(pending) // OCR_BATCH * OCR_BATCH
        batch = pending[:n]
        del pending[:n]
        for t, s in zip(batch, _ocr(batch, ocr, result) if batch else []):
            if (s or "").strip():
                pieces.append((t.page, t.y0, s.strip()))

    with fitz.open(stream=data, filetype="pdf") as doc:
        result.pages = len(doc)
        for page in doc:
            text = (page.get_text() or "").strip()
            if ocr == "off" or not _needs_ocr(page, text):
                if text:
                    pieces.append((page.number + 1, 0.0, text))
                continue
            ocr_pages[page.number + 1] = text
            result.ocr_pieces += _tile_count(page)
            if estimate_only:
                continue
            pending.extend(_tiles(page))
            read_pending(everything=False)
        if not estimate_only:
            read_pending(everything=True)
    if estimate_only:
        return result
    # OCR also reads the typed bits (e.g. a OneNote title); PyMuPDF's text is only a fallback.
    read_pages = {p for p, _, _ in pieces}
    pieces.extend((p, 0.0, t) for p, t in ocr_pages.items() if t and p not in read_pages)
    pieces.sort(key=lambda p: (p[0], p[1]))
    parts: list[str] = []
    offset = 0
    for k, (number, y, text) in enumerate(pieces):
        if parts:
            parts.append("\n\n")
            offset += 2
        nxt = pieces[k + 1] if k + 1 < len(pieces) else None
        y1 = nxt[1] if nxt and nxt[0] == number and nxt[1] > y else 1.0
        result.anchors.append((offset, number, y, y1))
        parts.append(text)
        offset += len(text)
    result.text = "".join(parts)
    result.method = "text" if not ocr_pages else ("ocr" if len(ocr_pages) == result.pages else "mixed")
    return result


# ---------- Storing ----------
def _chunks_with_positions(
    text: str, anchors: list[tuple[int, int, float, float]]
) -> list[tuple[str, int | None, float | None]]:
    """
    Passages with the page and spot (0..1) each one starts at. Inside a piece the spot is estimated
    from how far into the piece's text the passage starts (writing runs top to bottom).
    """
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    from backend.ingest_api import CHUNK_OVERLAP, CHUNK_SIZE

    splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP, add_start_index=True)
    offsets = [a[0] for a in anchors]
    out = []
    for d in splitter.create_documents([text]):
        if not anchors:
            out.append((d.page_content, None, None))
            continue
        start = max(int(d.metadata.get("start_index") or 0), 0)
        i = max(bisect.bisect_right(offsets, start) - 1, 0)
        piece_start, page, y0, y1 = anchors[i]
        piece_end = offsets[i + 1] if i + 1 < len(offsets) else len(text)
        share = min(max((start - piece_start) / max(piece_end - piece_start, 1), 0.0), 1.0)
        out.append((d.page_content, page, y0 + share * (y1 - y0)))
    return out


def _file_meta(entry: dict, status: str, **extra) -> dict:
    path = entry["path"]
    return {
        "path": path,
        "subject": subject_of(path),
        "etag": entry.get("etag") or "",
        "size": int(entry.get("size") or 0),
        "status": status,
        "updated_at": int(time.time()),
        **extra,
    }


def _store(entry: dict, pdf: PdfText) -> int:
    """Replace the passages and stored text of one PDF. Returns the number of passages."""
    from backend.ingest_api import embed_texts

    path = entry["path"]
    col = _notes(create=True)
    old_ids = get_all(col, where={"path": path}, include=[])["ids"]
    chunks = _chunks_with_positions(pdf.text, pdf.anchors)
    base = {
        "path": path,
        "subject": subject_of(path),
        "source_file": path.rsplit("/", 1)[-1],
        "label": label_of(path),
        "pages": int(pdf.pages),
        "library": 1,
    }
    new_ids: list[str] = []
    for i in range(0, len(chunks), 50):
        batch = chunks[i : i + 50]
        ids = [f"{_chunk_id_base(path)}_{i + j}" for j in range(len(batch))]
        metas = []
        for j, (_, page, y) in enumerate(batch):
            m = {**base, "chunk_index": i + j}
            if page is not None:
                m["page"] = int(page)
            if y is not None:
                m["y"] = round(float(y), 4)
            metas.append(m)
        col.upsert(
            ids=ids,
            documents=[c[0] for c in batch],
            embeddings=embed_texts([c[0] for c in batch]).tolist(),
            metadatas=metas,
        )
        new_ids.extend(ids)
    keep = set(new_ids)
    delete_ids(col, [x for x in old_ids if x not in keep])
    _write_parts(
        _docs(create=True),
        _doc_id(path),
        pdf.text,
        "library_file",
        _file_meta(
            entry,
            "ok",
            pages=pdf.pages,
            ocr_pieces=pdf.ocr_pieces,
            sarvam_pieces=pdf.sarvam_pieces,
            chunks=len(chunks),
            chars=len(pdf.text),
            method=pdf.method,
            note=",".join(sorted(set(pdf.notes))),
        ),
    )
    return len(chunks)


def _store_status(entry: dict, status: str, error: str = "") -> None:
    """Record a PDF that couldn't be indexed (yet), keeping any passages from an earlier version."""
    _write_parts(_docs(create=True), _doc_id(entry["path"]), "", "library_file", _file_meta(entry, status, error=error[:300]))


def remove(path: str) -> None:
    col = _notes(create=False)
    if col is not None:
        delete_ids(col, get_all(col, where={"path": path}, include=[])["ids"])
    docs = _docs(create=False)
    if docs is None:
        return
    doc_id = _doc_id(path)
    res = docs.get(ids=[doc_id], include=["metadatas"])
    if res.get("ids"):
        meta = (res.get("metadatas") or [{}])[0] or {}
        parts, version = int(meta.get("parts") or 0), str(meta.get("version") or "")
        delete_ids(docs, [f"{doc_id}#{version}#{k}" for k in range(parts)] + [doc_id])


# ---------- Sync ----------
_sync_lock = threading.Lock()
_state: dict = {"running": False, "current": None, "started_at": None, "finished_at": None, "last": None}
_status_cache: dict = {"at": 0.0, "value": None}


def sync_state() -> dict:
    return dict(_state)


def sync(
    dry_run: bool = False,
    only: list[str] | None = None,
    limit: int | None = None,
    force: bool = False,
    log=logger.info,
) -> dict:
    """
    Index new or changed PDFs and drop removed ones. `only` keeps paths starting with any of the
    given prefixes (and then nothing outside them is removed); `limit` caps how many files are read.
    Raises RuntimeError if another sync is already running.
    """
    if not _sync_lock.acquire(blocking=False):
        raise RuntimeError("A library sync is already running.")
    try:
        _state.update(running=True, current=None, started_at=time.time())
        mode = _ocr_mode()
        source = list_source_files()
        prefixes = [_clean(o) for o in (only or []) if _clean(o)]
        if prefixes:
            source = [e for e in source if any(e["path"] == o or e["path"].startswith(o) for o in prefixes)]
        known = indexed_files()
        source_paths = {e["path"] for e in source}

        def stale(e: dict) -> bool:
            # "empty" PDFs (nothing readable) are only re-read when they change: OCR costs money.
            k = known.get(e["path"])
            return force or not k or k.get("etag") != e["etag"] or k.get("status") in ("failed", "pending")

        todo = [e for e in source if stale(e)]
        if limit:
            todo = todo[: max(int(limit), 0)]
        gone = [] if prefixes else [p for p in known if p not in source_paths]
        summary = {
            "dry_run": dry_run,
            "ocr": mode,
            "source_files": len(source),
            "up_to_date": len(source) - len([e for e in source if stale(e)]),
            "to_index": len(todo),
            "indexed": 0,
            "empty": 0,
            "failed": 0,
            "waiting_for_budget": 0,
            "removed": 0,
            "pages": 0,
            "ocr_pieces": 0,
            "sarvam_pieces": 0,
        }
        budget_hit = False
        for n, entry in enumerate(todo, 1):
            path = entry["path"]
            _state["current"] = path
            try:
                data = download(path)
                if budget_hit and mode == "sarvam" and not dry_run:
                    pdf = read_pdf(data, ocr=mode, estimate_only=True)
                    if pdf.ocr_pieces:
                        raise BudgetReached("monthly AI budget reached")
                pdf = read_pdf(data, ocr=mode, estimate_only=dry_run)
            except BudgetReached as e:
                budget_hit = True
                summary["waiting_for_budget"] += 1
                if not dry_run and path not in known:
                    _store_status(entry, "pending", str(e))
                log(f"[{n}/{len(todo)}] {path}: waiting for next month's AI budget")
                continue
            except Exception as e:
                logger.exception("Library: could not read %s", path)
                summary["failed"] += 1
                # A PDF that was indexed before keeps its earlier passages and text.
                if not dry_run and known.get(path, {}).get("status") != "ok":
                    _store_status(entry, "failed", f"{type(e).__name__}: {e}")
                log(f"[{n}/{len(todo)}] {path}: failed ({type(e).__name__}: {e})")
                continue
            summary["pages"] += pdf.pages
            summary["ocr_pieces"] += pdf.ocr_pieces
            if dry_run:
                log(f"[{n}/{len(todo)}] {path}: {pdf.pages} page(s), {pdf.ocr_pieces} piece(s) to OCR")
                continue
            if pdf.sarvam_pieces:
                summary["sarvam_pieces"] += pdf.sarvam_pieces
                usage.record(USAGE_ID, pages_read=pdf.sarvam_pieces)
            if not pdf.text.strip():
                summary["empty"] += 1
                _store_status(entry, "empty", "No text could be read from this PDF.")
                log(f"[{n}/{len(todo)}] {path}: no text found")
                continue
            chunks = _store(entry, pdf)
            summary["indexed"] += 1
            log(f"[{n}/{len(todo)}] {path}: {chunks} passages ({pdf.method}, {pdf.ocr_pieces} OCR pieces)")
        if not dry_run:
            for p in gone:
                remove(p)
                log(f"removed {p}")
            summary["removed"] = len(gone)
        else:
            summary["would_remove"] = len(gone)
        _state["last"] = summary
        _status_cache["at"] = 0.0
        return summary
    finally:
        _state.update(running=False, current=None, finished_at=time.time())
        _sync_lock.release()


def tesseract_fallbacks() -> list[str]:
    """PDFs where Sarvam failed and Tesseract was used instead (worth reading again)."""
    return sorted(p for p, m in indexed_files().items() if TESSERACT_FALLBACK in (m.get("note") or ""))


def _sync_quietly(**kwargs) -> None:
    try:
        summary = sync(**kwargs)
        logger.info("Library sync finished: %s", summary)
    except RuntimeError as e:
        logger.info("%s", e)
    except Exception as e:
        logger.exception("Library sync failed")
        from backend import alerts

        alerts.notify("library-sync", f"Library sync failed: {type(e).__name__}: {e}")


def sync_in_background(force: bool = False) -> bool:
    """Start a sync in a background thread. False if one is already running."""
    if _state["running"]:
        return False
    threading.Thread(target=_sync_quietly, kwargs={"force": force}, name="library-sync", daemon=True).start()
    return True


_started = False
_start_lock = threading.Lock()


def start() -> None:
    """Re-check the bucket every LIBRARY_SYNC_HOURS (off unless set: reading pages with Sarvam costs money)."""
    global _started
    with _start_lock:
        if _started or not settings.LIBRARY_ENABLED or settings.LIBRARY_SYNC_HOURS <= 0:
            return
        _started = True

    def loop() -> None:
        while True:
            _sync_quietly()
            time.sleep(max(settings.LIBRARY_SYNC_HOURS, 0.25) * 3600)

    threading.Thread(target=loop, name="library-sync-loop", daemon=True).start()


def public_status() -> dict:
    """What the client needs to know: which PDFs can be asked about (cached for a minute)."""
    if not settings.LIBRARY_ENABLED:
        return {"enabled": False, "ready": [], "syncing": False}
    now = time.time()
    if _status_cache["value"] is None or now - _status_cache["at"] > STATUS_CACHE_SECONDS:
        files = indexed_files()
        _status_cache["value"] = sorted(p for p, m in files.items() if m.get("status") == "ok")
        _status_cache["at"] = now
    return {"enabled": True, "ready": list(_status_cache["value"]), "syncing": bool(_state["running"])}


# ---------- Search + study context ----------
def search(q_emb: list[float], where: dict, n_results: int) -> list[dict]:
    """Best library passages matching `where`, scored as cosine similarity (same scale as notes)."""
    if not settings.LIBRARY_ENABLED:
        return []
    col = _notes(create=False)
    if col is None:
        return []
    try:
        res = col.query(
            query_embeddings=[q_emb],
            n_results=n_results,
            include=["documents", "metadatas", "distances"],
            where=where,
        )
    except Exception:
        logger.warning("Library search failed", exc_info=True)
        return []
    space = collection_distance_space(col)
    ids = (res.get("ids") or [[]])[0] or []
    docs = (res.get("documents") or [[]])[0] or []
    metas = (res.get("metadatas") or [[]])[0] or []
    dists = (res.get("distances") or [[]])[0] or []
    out = []
    for i, cid in enumerate(ids):
        content = (docs[i] if i < len(docs) else "") or ""
        if not content.strip():
            continue
        md = metas[i] if i < len(metas) and isinstance(metas[i], dict) else {}
        dist = float(dists[i]) if i < len(dists) and dists[i] is not None else 2.0
        out.append({"id": cid, "content": content, "metadata": md, "_score": distance_to_similarity(dist, space)})
    out.sort(key=lambda d: d["_score"], reverse=True)
    return out


def study_context(library_path: str, focus: str, include_subject: bool) -> tuple[str | None, str | None]:
    """Study-tool context from a library PDF (plus short parts of its course), like the notes version."""
    file_path, subject = scope_of(library_path)
    if not subject:
        return None, "Open a course PDF first."
    ready = sorted(p for p, m in indexed_files().items() if m.get("subject") == subject and m.get("status") == "ok")
    parts = [f"Focus: {focus}", f"Course: {subject_label(subject)}"]
    if file_path:
        main = document_text(file_path)
        if not main:
            return None, NOT_READY
        parts += [f"Primary file: {label_of(file_path)}", "", "--- PRIMARY FILE CONTENT ---", main[:35000]]
        extras, budget, per_file = ([p for p in ready if p != file_path] if include_subject else []), 25000, 3500
    else:
        if not ready:
            return None, NOT_READY
        parts += ["", "--- PRIMARY FILE CONTENT ---"]
        extras, budget, per_file = ready, 50000, max(1500, 50000 // len(ready))
    added: list[str] = []
    for p in extras:
        if budget <= 0:
            break
        text = document_text(p)
        if not text:
            continue
        piece = text[: min(per_file, budget)]
        added.append(f"--- {'COURSE SUPPORTING FILE' if file_path else 'FILE'}: {label_of(p)} ---\n{piece}")
        budget -= len(piece)
    if added:
        if file_path:
            parts += ["", "--- OPTIONAL SAME-COURSE SUPPORT ---"]
        parts.append("\n\n".join(added))
    elif not file_path:
        return None, NOT_READY
    return "\n".join(parts)[:60000], None
