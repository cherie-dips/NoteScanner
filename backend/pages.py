"""
Page numbers for PDF text, so answers can say "page 7" instead of only the file name.

A file's text gets a list of page starts: [(character offset, page number), ...]. Each search chunk
then records the page its first character is on. When the text comes from PyMuPDF the starts are
exact; when it comes from Sarvam Vision, each page's own text is found inside the full text.
"""
from bisect import bisect_right

PageStarts = list[tuple[int, int]]

_IGNORED = set("#*_`>|")


def join_pages(page_texts: list[str]) -> tuple[str, PageStarts]:
    """Join per-page texts into one text and record where each page starts."""
    parts: list[str] = []
    starts: PageStarts = []
    offset = 0
    for number, page in enumerate(page_texts, start=1):
        page = (page or "").strip()
        if not page:
            continue
        if parts:
            parts.append("\n\n")
            offset += 2
        starts.append((offset, number))
        parts.append(page)
        offset += len(page)
    return "".join(parts), starts


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Lowercase, collapse whitespace and drop markdown symbols, remembering each char's original index."""
    out: list[str] = []
    index_map: list[int] = []
    prev_space = True
    for i, ch in enumerate(text or ""):
        if ch.isspace() or ch in _IGNORED:
            if not prev_space:
                out.append(" ")
                index_map.append(i)
                prev_space = True
            continue
        out.append(ch.lower())
        index_map.append(i)
        prev_space = False
    return "".join(out), index_map


def anchor_page_starts(full_text: str, page_texts: list[str], snippet_len: int = 40) -> PageStarts | None:
    """
    Find where each page's text begins inside `full_text` (searching forward, in page order).
    Pages whose start can't be found are merged into the page before. None if nothing useful.
    """
    pages = [(n, p) for n, p in enumerate(page_texts or [], start=1)]
    if len(pages) < 2:
        return None
    norm, index_map = _normalize_with_map(full_text)
    starts: PageStarts = [(0, pages[0][0])]
    pos = 0
    for number, page in pages[1:]:
        snippet = _normalize_with_map(page)[0].strip()[:snippet_len]
        if len(snippet) < 12:
            continue
        found = norm.find(snippet, pos)
        if found < 0:
            continue
        pos = found
        starts.append((index_map[found], number))
    return starts if len(starts) > 1 else None


def page_at(offset: int, starts: PageStarts | None) -> int | None:
    if not starts:
        return None
    i = bisect_right([s for s, _ in starts], offset) - 1
    return starts[max(i, 0)][1]


def pdf_page_texts(data: bytes) -> list[str]:
    import fitz  # PyMuPDF

    doc = fitz.open(stream=data, filetype="pdf")
    try:
        return [page.get_text() for page in doc]
    finally:
        doc.close()


def pdf_page_count(data: bytes) -> int:
    try:
        return len(pdf_page_texts(data))
    except Exception:
        return 1
