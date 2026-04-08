"""LLM + OCR: Sarvam AI — Document Intelligence (Sarvam Vision) + Chat Completions."""
import json
import os
import re
import tempfile
import unicodedata
import zipfile
from typing import Any

import httpx

# Chat models (see https://docs.sarvam.ai/api-reference-docs/api-guides-tutorials/chat-completion/overview)
_DEFAULT_MODEL_RAG = "sarvam-105b"
_DEFAULT_MODEL_STUDY = "sarvam-30b"

_GROUNDING_STOPWORDS = {
    "about", "after", "again", "also", "among", "another", "because", "before", "being", "between",
    "both", "could", "does", "each", "from", "have", "into", "more", "most", "other", "over", "same",
    "some", "such", "than", "that", "their", "there", "these", "they", "this", "those", "through",
    "under", "using", "very", "what", "when", "where", "which", "while", "with", "would",
}


def _sarvam_api_key() -> str | None:
    k = os.getenv("SARVAM_API_KEY")
    return k.strip() if k else None


def _chat_base_url() -> str:
    return os.getenv("SARVAM_API_BASE", "https://api.sarvam.ai").rstrip("/")


def _model_rag() -> str:
    return os.getenv("SARVAM_MODEL_RAG", _DEFAULT_MODEL_RAG).strip()


def _model_study() -> str:
    return os.getenv("SARVAM_MODEL_STUDY", _DEFAULT_MODEL_STUDY).strip()


def _sarvam_chat_complete(
    messages: list[dict[str, str]],
    *,
    model: str,
    max_tokens: int,
    temperature: float,
) -> tuple[str | None, str | None]:
    key = _sarvam_api_key()
    if not key:
        return None, "SARVAM_API_KEY not configured"
    url = f"{_chat_base_url()}/v1/chat/completions"
    try:
        r = httpx.post(
            url,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            timeout=300.0,
        )
        data = r.json()
        if r.status_code >= 400:
            err = data.get("error") if isinstance(data.get("error"), dict) else {}
            msg = err.get("message") if isinstance(err, dict) else None
            return None, msg or r.text or f"HTTP {r.status_code}"
        choices = data.get("choices") or []
        if not choices:
            return None, "no choices in response"
        content = (choices[0].get("message") or {}).get("content")
        if content is None or not str(content).strip():
            return None, "empty model content"
        return str(content).strip(), None
    except httpx.RequestError as e:
        return None, str(e)
    except Exception as e:
        return None, str(e)


def _prepare_image_for_document_intel(image_bytes: bytes, mime: str) -> tuple[bytes, str]:
    """Document Intelligence accepts PNG/JPEG/PDF; normalize webp/gif to JPEG."""
    m = (mime or "image/jpeg").lower()
    if m in ("image/webp", "image/gif"):
        from io import BytesIO

        from PIL import Image

        im = Image.open(BytesIO(image_bytes)).convert("RGB")
        out = BytesIO()
        im.save(out, format="JPEG", quality=92)
        return out.getvalue(), ".jpg"
    if m == "image/png":
        return image_bytes, ".png"
    return image_bytes, ".jpg"


def _prepare_document_for_document_intel(file_bytes: bytes, mime: str) -> tuple[bytes, str]:
    """Normalize uploads for Sarvam Document Intelligence (image or PDF)."""
    m = (mime or "").lower().strip()
    if m == "application/pdf":
        return file_bytes, ".pdf"
    return _prepare_image_for_document_intel(file_bytes, m or "image/jpeg")


def _extract_markdown_from_output_zip(zip_path: str) -> str:
    with zipfile.ZipFile(zip_path, "r") as z:
        names = sorted(z.namelist())
        md_names = [n for n in names if n.lower().endswith(".md")]
        html_names = [n for n in names if n.lower().endswith((".html", ".htm"))]
        pick = (md_names[0] if md_names else None) or (html_names[0] if html_names else None) or (
            names[0] if names else None
        )
        if not pick:
            return ""
        return z.read(pick).decode("utf-8", errors="replace").strip()


def _document_intelligence_image(image_bytes: bytes, mime: str) -> tuple[str | None, str | None]:
    """
    OCR / layout text via Sarvam Document Intelligence (Sarvam Vision).
    See: https://docs.sarvam.ai/api-reference-docs/api-guides-tutorials/document-intelligence/overview
    """
    key = _sarvam_api_key()
    if not key:
        return None, "SARVAM_API_KEY not configured"
    try:
        from sarvamai import SarvamAI
    except ImportError:
        return None, "sarvamai package not installed (pip install sarvamai)"

    raw_bytes, suffix = _prepare_image_for_document_intel(image_bytes, mime)
    tmp_image: str | None = None
    tmp_zip: str | None = None
    timeout = float(os.getenv("SARVAM_DOC_INTEL_TIMEOUT", "180"))
    try:
        fd, tmp_image = tempfile.mkstemp(suffix=suffix)
        os.write(fd, raw_bytes)
        os.close(fd)
        fd = -1

        client = SarvamAI(api_subscription_key=key, timeout=timeout)
        lang = os.getenv("SARVAM_DOC_INTEL_LANGUAGE", "en-IN").strip()
        job = client.document_intelligence.create_job(language=lang, output_format="md")
        job.upload_file(tmp_image)
        job.start()
        status = job.wait_until_complete(timeout=timeout)
        state = str(getattr(status, "job_state", "") or "")
        if state not in ("Completed", "PartiallyCompleted"):
            return None, f"Document intelligence job state: {state or 'unknown'}"

        zfd, tmp_zip = tempfile.mkstemp(suffix=".zip")
        os.close(zfd)
        job.download_output(tmp_zip)
        text = _extract_markdown_from_output_zip(tmp_zip)
        if not text:
            return None, "empty document intelligence output"
        return text, None
    except TimeoutError as e:
        return None, str(e)
    except Exception as e:
        return None, str(e)
    finally:
        if tmp_image and os.path.isfile(tmp_image):
            try:
                os.unlink(tmp_image)
            except OSError:
                pass
        if tmp_zip and os.path.isfile(tmp_zip):
            try:
                os.unlink(tmp_zip)
            except OSError:
                pass


def transcribe_handwritten_image(image_bytes: bytes, mime: str = "image/jpeg") -> tuple[str | None, str | None]:
    """Sarvam Vision (Document Intelligence) → markdown/plain text from note images."""
    return _document_intelligence_image(image_bytes, mime)


def transcribe_document_bytes(file_bytes: bytes, mime: str = "application/pdf") -> tuple[str | None, str | None]:
    """Sarvam Vision (Document Intelligence) OCR for uploaded PDFs/images."""
    key = _sarvam_api_key()
    if not key:
        return None, "SARVAM_API_KEY not configured"
    try:
        from sarvamai import SarvamAI
    except ImportError:
        return None, "sarvamai package not installed (pip install sarvamai)"
    raw_bytes, suffix = _prepare_document_for_document_intel(file_bytes, mime)
    tmp_file: str | None = None
    tmp_zip: str | None = None
    timeout = float(os.getenv("SARVAM_DOC_INTEL_TIMEOUT", "180"))
    try:
        fd, tmp_file = tempfile.mkstemp(suffix=suffix)
        os.write(fd, raw_bytes)
        os.close(fd)
        client = SarvamAI(api_subscription_key=key, timeout=timeout)
        lang = os.getenv("SARVAM_DOC_INTEL_LANGUAGE", "en-IN").strip()
        job = client.document_intelligence.create_job(language=lang, output_format="md")
        job.upload_file(tmp_file)
        job.start()
        status = job.wait_until_complete(timeout=timeout)
        state = str(getattr(status, "job_state", "") or "")
        if state not in ("Completed", "PartiallyCompleted"):
            return None, f"Document intelligence job state: {state or 'unknown'}"
        zfd, tmp_zip = tempfile.mkstemp(suffix=".zip")
        os.close(zfd)
        job.download_output(tmp_zip)
        text = _extract_markdown_from_output_zip(tmp_zip)
        if not text:
            return None, "empty document intelligence output"
        return text, None
    except TimeoutError as e:
        return None, str(e)
    except Exception as e:
        return None, str(e)
    finally:
        if tmp_file and os.path.isfile(tmp_file):
            try:
                os.unlink(tmp_file)
            except OSError:
                pass
        if tmp_zip and os.path.isfile(tmp_zip):
            try:
                os.unlink(tmp_zip)
            except OSError:
                pass


def sarvam_general_answer(question: str) -> tuple[str | None, str | None]:
    """Answer without retrieving text from Chroma (no RAG / no stored course excerpts)."""
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "You are a clear, accurate teaching assistant. Answer the student's question directly. "
                "If essential information is missing, say what you would need to know."
            ),
        },
        {"role": "user", "content": question},
    ]
    return _sarvam_chat_complete(
        messages,
        model=_model_rag(),
        max_tokens=8192,
        temperature=0.3,
    )


def sarvam_rag_answer(question: str, context: str) -> tuple[str | None, str | None]:
    """Answer strictly from provided notes context."""
    ctx = (context or "").strip()[:60000]
    q = (question or "").strip()
    if not q:
        return None, "Empty query"
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "You are a teaching assistant answering ONLY from the provided notes excerpts. "
                "Do not claim inability to access files. "
                "If answer is not present in the excerpts, say that clearly and suggest what to open/upload."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Question:\n{q}\n\n"
                f"Notes excerpts:\n{ctx}\n\n"
                "Answer using only these excerpts."
            ),
        },
    ]
    return _sarvam_chat_complete(
        messages,
        model=_model_rag(),
        max_tokens=4096,
        temperature=0.2,
    )


def _parse_json_loose(raw: str) -> Any:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```\s*$", "", raw)
    return json.loads(raw)


def _tokens(text: str) -> list[str]:
    # 3+ chars: math/CS terms like "ref", "rref", "row"
    return [
        t
        for t in re.findall(r"[a-zA-Z][a-zA-Z0-9]{2,}", (text or "").lower())
        if t not in _GROUNDING_STOPWORDS
    ]


def _primary_body_from_study_ctx(ctx: str) -> str:
    marker = "--- PRIMARY FILE CONTENT ---"
    i = (ctx or "").find(marker)
    if i >= 0:
        return (ctx[i + len(marker) :]).lstrip()
    return ctx or ""


def _context_keywords(context: str) -> set[str]:
    toks = _tokens(context)
    if not toks:
        return set()
    freq: dict[str, int] = {}
    for t in toks:
        freq[t] = freq.get(t, 0) + 1
    ranked = sorted(freq.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0]))
    return {k for k, _v in ranked[:250]}


def _grounded_enough(text: str, keywords: set[str], *, min_hits: int = 2) -> bool:
    if not keywords:
        return False
    hits = len(set(_tokens(text)) & keywords)
    return hits >= min_hits


def _normalize_study_match(s: str) -> str:
    t = unicodedata.normalize("NFKC", s or "")
    for u, a in (
        ("\u2013", "-"),
        ("\u2014", "-"),
        ("\u2212", "-"),
        ("\u00a0", " "),
        ("\u200b", ""),
    ):
        t = t.replace(u, a)
    return re.sub(r"\s+", " ", t.strip().lower())


def _contains_context_quote(context: str, quote: str) -> bool:
    q = (quote or "").strip()
    if len(q) < 12:
        return False
    ctx_raw = context or ""
    # Fast path: exact / case-insensitive substring of raw notes (handles OCR spacing).
    if q in ctx_raw:
        return True
    if q.lower() in ctx_raw.lower():
        return True
    ctx_n = _normalize_study_match(context)
    qq = _normalize_study_match(q)
    if len(qq) < 12:
        return False
    if qq in ctx_n:
        return True
    # Model may drop punctuation around matrices / LaTeX; alnum fold still ties to notes.
    def fold(t: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", t)

    qf, cf = fold(qq), fold(ctx_n)
    return len(qf) >= 10 and qf in cf


def _pick_evidence_span(body: str, text_for_match: str, *, min_len: int = 12, max_len: int = 240) -> str:
    """Choose a substring of body that best overlaps tokens from text_for_match (server-side evidence)."""
    b = body or ""
    if len(b) < min_len:
        return ""
    toks = [t for t in _tokens(text_for_match) if len(t) >= 3][:50]
    win = min(max_len, len(b))
    win = max(min_len, win)
    step = max(40, win // 4)
    best_i, best_score = 0, -1
    upper = max(1, len(b) - win + 1)
    for i in range(0, upper, step):
        chunk = b[i : i + win]
        cl = chunk.lower()
        score = sum(1 for t in toks if t in cl) if toks else len(chunk)
        if score > best_score:
            best_score = score
            best_i = i
    span = b[best_i : best_i + win].strip()
    if len(span) < min_len:
        span = b[: min(max_len, len(b))].strip()
    out = span[:max_len] if span else ""
    if out and not _contains_context_quote(b, out):
        out = b[: min(max_len, len(b))].strip()[:max_len]
    return out


def _best_line_snippet(body: str, anchor_text: str, max_len: int = 220) -> str:
    """Return a note line (or start of body) that best overlaps anchor tokens."""
    b = body or ""
    if len(b) < 12:
        return ""
    want = set(_tokens(anchor_text))
    best_s, best_score = "", -1
    for line in b.splitlines():
        s = line.strip()
        if len(s) < 12:
            continue
        got = set(_tokens(s))
        score = len(want & got) if want else len(s)
        if score > best_score:
            best_score, best_s = score, s
    if best_s and _contains_context_quote(b, best_s[:max_len]):
        return best_s[:max_len].strip()
    head = b[:max_len].strip()
    return head if len(head) >= 12 else ""


def _token_overlap(a: str, b: str) -> int:
    return len(set(_tokens(a)) & set(_tokens(b)))


def _finalize_evidence(primary_body: str, evidence: str, anchor_text: str, keywords: set[str]) -> str | None:
    """Return evidence substring of primary_body: model quote, picked span, best line, or note head."""
    body = (primary_body or "").strip()
    if len(body) < 12:
        return None
    ev = (evidence or "").strip()
    if ev and _contains_context_quote(body, ev):
        return ev[:500]
    picked = _pick_evidence_span(body, anchor_text)
    if picked and _contains_context_quote(body, picked):
        return picked[:500]
    line_snip = _best_line_snippet(body, anchor_text)
    if line_snip and _contains_context_quote(body, line_snip):
        return line_snip[:500]
    if _grounded_enough(anchor_text, keywords, min_hits=1):
        head = body[: min(220, len(body))].strip()
        if len(head) >= 12 and _contains_context_quote(body, head):
            return head
    return None


def _validate_flashcards(
    items: Any, n: int, keywords: set[str], ctx: str, primary_body: str
) -> list[dict[str, str]] | None:
    if not isinstance(items, list):
        return None
    body = (primary_body or _primary_body_from_study_ctx(ctx) or ctx).strip()
    cleaned: list[dict[str, str]] = []
    for it in items:
        if len(cleaned) >= n:
            break
        if not isinstance(it, dict):
            continue
        front = str(it.get("front") or "").strip()
        back = str(it.get("back") or "").strip()
        if not front or not back:
            continue
        ev_raw = str(it.get("evidence") or "").strip()
        evidence = _finalize_evidence(body, ev_raw, front + " " + back, keywords)
        if not evidence:
            continue
        cleaned.append({"front": front, "back": back, "evidence": evidence})
    return cleaned if len(cleaned) >= 1 else None


def _validate_mcq(items: Any, n: int, keywords: set[str], ctx: str, primary_body: str) -> list[dict[str, Any]] | None:
    if not isinstance(items, list):
        return None
    body = (primary_body or _primary_body_from_study_ctx(ctx) or ctx).strip()
    cleaned: list[dict[str, Any]] = []
    for it in items:
        if len(cleaned) >= n:
            break
        if not isinstance(it, dict):
            continue
        q = str(it.get("question") or "").strip()
        opts = it.get("options")
        ai = it.get("answer_index")
        if not q or not isinstance(opts, list) or len(opts) != 4:
            continue
        try:
            ai_i = int(ai)
        except Exception:
            continue
        if ai_i < 0 or ai_i > 3:
            continue
        norm_opts = [str(x or "").strip() for x in opts]
        if any(not o for o in norm_opts):
            continue
        anchor = " ".join([q] + norm_opts)
        ev_raw = str(it.get("evidence") or "").strip()
        evidence = _finalize_evidence(body, ev_raw, anchor, keywords)
        if not evidence:
            continue
        cleaned.append(
            {
                "question": q,
                "options": norm_opts,
                "answer_index": ai_i,
                "evidence": evidence,
            }
        )
    return cleaned if len(cleaned) >= 1 else None


def _coerce_study_list(parsed: Any) -> list | None:
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for k in ("items", "flashcards", "cards", "mcq", "mcqs", "questions", "data", "results"):
            v = parsed.get(k)
            if isinstance(v, list):
                return v
    return None


def _synthetic_flashcards_from_body(body: str, n: int) -> list[dict[str, str]]:
    b = (body or "").strip()
    if len(b) < 12:
        return []
    chunks = re.split(r"\n{2,}|(?<=[.!?])\s+", b)
    parts = [p.strip() for p in chunks if len(p.strip()) >= 25]
    out: list[dict[str, str]] = []
    for i in range(0, len(parts), 2):
        if len(out) >= n:
            break
        front = parts[i][:220]
        back = (parts[i + 1] if i + 1 < len(parts) else parts[i])[:400]
        ev = parts[i][: min(180, len(parts[i]))]
        if len(ev) < 12:
            ev = b[:120]
        if len(front) < 3 or len(back) < 3:
            continue
        out.append({"front": front, "back": back, "evidence": ev})
    if not out:
        head = b[: min(400, len(b))]
        ev = b[: min(120, len(b))]
        if len(ev) >= 12:
            out.append({"front": "Notes excerpt", "back": head, "evidence": ev})
    return out[:n]


def _synthetic_mcq_from_body(body: str, n: int) -> list[dict[str, Any]]:
    b = (body or "").strip()
    if len(b) < 12:
        return []
    sentences = [s.strip() for s in re.split(r"[.!?\n]+", b) if len(s.strip()) > 35]
    out: list[dict[str, Any]] = []
    for s in sentences[:n]:
        q = f"Which statement matches the notes? {s[:100]}…"
        o0 = s[: min(90, len(s))] + ("…" if len(s) > 90 else "")
        opts = [
            o0,
            "The notes do not discuss this topic.",
            "The opposite of what the notes state.",
            "None of the above.",
        ]
        ev = s[: min(200, len(s))]
        if len(ev) < 12:
            ev = b[:120]
        out.append({"question": q[:400], "options": opts, "answer_index": 0, "evidence": ev})
    return out


def _synthetic_mind_map(body: str) -> dict[str, Any]:
    b = (body or "").strip()
    lines = [ln.strip() for ln in b.splitlines() if 18 <= len(ln.strip()) <= 220][:14]
    if len(lines) < 2:
        step = 60
        lines = [b[i : i + step].strip() for i in range(0, min(len(b), 480), step) if len(b[i : i + step].strip()) >= 12][
            :10
        ]
    if len(lines) < 2:
        return {"nodes": [], "edges": []}
    nodes = [{"id": str(i + 1), "label": lines[i][:90], "source_quote": lines[i][:220]} for i in range(len(lines))]
    edges = [{"source": str(i), "target": str(i + 1), "label": ""} for i in range(1, len(nodes))]
    return {"nodes": nodes, "edges": edges}


def cheap_study_json(context: str, task: str, n: int) -> tuple[Any, str | None]:
    ctx = context[:60000]
    body = _primary_body_from_study_ctx(ctx)
    keywords = _context_keywords(body or ctx)
    if len(keywords) < 6:
        return None, "Not enough usable note content for grounded study generation."
    if task == "flashcards":
        prompt = f"""You are generating study material ONLY from the provided notes context.
STRICT RULES:
- Use only facts/concepts that appear in the context.
- Do NOT use outside knowledge, generic filler, or random topics.
- If context is insufficient, still stay within context and produce shorter/simpler cards.
- Return EXACTLY {n} items.
- Output ONLY a JSON array of objects with keys "front", "back", "evidence". No markdown fence.
- "evidence" must be a contiguous quote copied from the PRIMARY FILE CONTENT section (>= 12 chars). Copy punctuation and wording literally when possible.
- Include exact terminology/phrases from the notes where possible.

Notes context:
{ctx}
"""
    else:
        prompt = f"""You are generating study material ONLY from the provided notes context.
STRICT RULES:
- Use only facts/concepts that appear in the context.
- Do NOT use outside knowledge, generic filler, or random topics.
- Keep questions grounded in the notes wording/ideas.
- Return EXACTLY {n} items.
- Output ONLY a JSON array of objects with keys "question", "options" (array of 4 strings), "answer_index" (0-3), "evidence". No markdown fence.
- "evidence" must be a contiguous quote copied from the PRIMARY FILE CONTENT section (>= 12 chars). Copy punctuation and wording literally when possible.
- Include exact terminology/phrases from the notes where possible.

Notes context:
{ctx}
"""
    attempts = 3
    last_err = "generation failed"
    for k in range(attempts):
        msg = prompt if k == 0 else (prompt + "\n\nPrevious output was not grounded in notes. Regenerate with stricter note-anchored content.")
        raw, err = _sarvam_chat_complete(
            [{"role": "user", "content": msg}],
            model=_model_study(),
            max_tokens=8192,
            temperature=0.05,
        )
        if err:
            last_err = err
            continue
        if not raw:
            last_err = "empty model output"
            continue
        try:
            parsed = _parse_json_loose(raw)
            coerced = _coerce_study_list(parsed)
            if coerced is None:
                last_err = "Model returned JSON but not a list of study items."
                continue
            if task == "flashcards":
                valid = _validate_flashcards(coerced, n, keywords, ctx, body)
            else:
                valid = _validate_mcq(coerced, n, keywords, ctx, body)
            if valid is not None and len(valid) >= 1:
                return valid, None
            last_err = "Model output was not grounded in selected notes."
        except json.JSONDecodeError as e:
            last_err = f"invalid JSON: {e}"
    primary = (body or _primary_body_from_study_ctx(ctx) or "").strip()
    if len(primary) >= 12:
        if task == "flashcards":
            syn = _synthetic_flashcards_from_body(primary, n)
        else:
            syn = _synthetic_mcq_from_body(primary, n)
        if syn:
            return syn, None
    return None, last_err


def _prune_mind_map(data: dict, primary_body: str, keywords: set[str]) -> dict | None:
    """Keep nodes grounded in notes; repair or drop bad source_quote; filter edges to kept ids."""
    body = (primary_body or "").strip()
    if len(body) < 12:
        return None
    nodes_raw = data.get("nodes") or []
    edges_raw = data.get("edges") or []
    if not isinstance(nodes_raw, list):
        return None
    kept: list[dict[str, str]] = []
    for n in nodes_raw:
        if not isinstance(n, dict):
            continue
        nid = str(n.get("id") or "").strip()
        label = str(n.get("label") or "").strip()
        quote = str(n.get("source_quote") or "").strip()
        if not nid or not label:
            continue
        final_quote = quote if quote and _contains_context_quote(body, quote) else ""
        if not final_quote:
            final_quote = _pick_evidence_span(body, label + " " + quote)
        if not final_quote or not _contains_context_quote(body, final_quote):
            continue
        kept.append({"id": nid, "label": label, "source_quote": final_quote})
    if len(kept) < 2:
        return None
    kept_ids = {n["id"] for n in kept}
    kept_edges: list[dict[str, str]] = []
    if isinstance(edges_raw, list):
        for e in edges_raw:
            if not isinstance(e, dict):
                continue
            s, t = str(e.get("source") or ""), str(e.get("target") or "")
            if s in kept_ids and t in kept_ids:
                kept_edges.append(
                    {
                        "source": s,
                        "target": t,
                        "label": str(e.get("label") or ""),
                    }
                )
    return {"nodes": kept, "edges": kept_edges}


def mind_map_json(context: str) -> tuple[dict | None, str | None]:
    ctx = context[:60000]
    body = _primary_body_from_study_ctx(ctx)
    keywords = _context_keywords(body or ctx)
    if len(keywords) < 6:
        return None, "Not enough usable note content for grounded mind map generation."
    prompt = f"""Build a mind map ONLY from the provided notes context.
STRICT RULES:
- Use only concepts present in the context.
- Do NOT add outside topics or generic unrelated nodes.
- Reuse notes terminology in node labels.

Output a single JSON object with:
"nodes": [ {{"id": string, "label": string, "source_quote": string}} ],
"edges": [ {{"source": string, "target": string, "label": string}} ]
Use 8-25 nodes max. IDs must match between edges and nodes. No markdown fence.
"source_quote" must be a contiguous quote from PRIMARY FILE CONTENT (>= 12 chars).

Notes context:
{ctx}
"""
    last_err = "generation failed"
    for k in range(3):
        msg = prompt if k == 0 else (prompt + "\n\nPrevious output used unrelated topics. Regenerate strictly from notes context.")
        raw, err = _sarvam_chat_complete(
            [{"role": "user", "content": msg}],
            model=_model_study(),
            max_tokens=4096,
            temperature=0.05,
        )
        if err:
            last_err = err
            continue
        if not raw:
            last_err = "empty model output"
            continue
        try:
            data = _parse_json_loose(raw)
            if not isinstance(data, dict):
                last_err = "invalid shape"
                continue
            nodes = data.get("nodes")
            if not isinstance(nodes, list) or not nodes:
                last_err = "invalid shape"
                continue
            pruned = _prune_mind_map(data, body, keywords)
            if pruned is None:
                last_err = "Mind map was not grounded in selected notes."
                continue
            return pruned, None
        except json.JSONDecodeError as e:
            last_err = f"invalid JSON: {e}"
    primary = (body or _primary_body_from_study_ctx(ctx) or "").strip()
    if len(primary) >= 40:
        syn = _synthetic_mind_map(primary)
        if syn.get("nodes") and len(syn["nodes"]) >= 2:
            return syn, None
    return None, last_err


