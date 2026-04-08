"""LLM + OCR: Sarvam AI — Document Intelligence (Sarvam Vision) + Chat Completions."""
import json
import os
import re
import tempfile
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
    """Answer from notes-first context with constrained subject verification."""
    ctx = (context or "").strip()[:60000]
    q = (question or "").strip()
    if not q:
        return None, "Empty query"
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "You are a brilliant subject-matter tutor helping a student prepare for exams.\n\n"
                "GROUNDING RULES:\n"
                "- The student's notes are your PRIMARY source of truth.\n"
                "- Identify the subject/topic from the notes (e.g. Machine Learning, Linear Algebra, Operating Systems).\n"
                "- ALWAYS answer the student's question — never refuse or say 'not in notes'.\n"
                "- When the notes contain the answer, cite specific details from them.\n"
                "- When the notes are incomplete, supplement with correct, textbook-level knowledge for THAT subject.\n"
                "  Mark such additions with '📖 Beyond notes:' so the student knows.\n"
                "- NEVER drift to unrelated subjects. If asked about something outside the course scope, "
                "  briefly redirect: 'That's outside [subject]. Here's what your notes cover instead…'\n\n"
                "TEACHING STYLE:\n"
                "- Explain like a great tutor: clear, concise, with examples.\n"
                "- For problem-solving: show step-by-step working.\n"
                "- For tricky exam practice: generate 5-8 challenging questions with concise answer keys.\n"
                "- Use bullet points and short paragraphs for readability.\n"
                "- Do not claim inability to access files or notes."
            ),
        },
        {
            "role": "user",
            "content": (
                f"**Student's question:** {q}\n\n"
                f"**Notes excerpts:**\n{ctx}\n\n"
                "Answer the question fully. Use notes evidence first, then subject knowledge if needed."
            ),
        },
    ]
    return _sarvam_chat_complete(
        messages,
        model=_model_rag(),
        max_tokens=4096,
        temperature=0.3,
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


def _validate_flashcards_smart(items: Any, n: int) -> list[dict[str, str]] | None:
    """Accept flashcards with valid front/back; no verbatim evidence requirement."""
    if not isinstance(items, list):
        return None
    cleaned: list[dict[str, str]] = []
    for it in items:
        if len(cleaned) >= n:
            break
        if not isinstance(it, dict):
            continue
        front = str(it.get("front") or "").strip()
        back = str(it.get("back") or "").strip()
        if len(front) < 5 or len(back) < 5:
            continue
        source = str(it.get("source") or "notes").strip()
        cleaned.append({"front": front, "back": back, "source": source})
    return cleaned if len(cleaned) >= 1 else None


def _validate_mcq_smart(items: Any, n: int) -> list[dict[str, Any]] | None:
    """Accept MCQs with valid structure; no verbatim evidence requirement."""
    if not isinstance(items, list):
        return None
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
        source = str(it.get("source") or "notes").strip()
        cleaned.append({
            "question": q,
            "options": norm_opts,
            "answer_index": ai_i,
            "source": source,
        })
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



def cheap_study_json(context: str, task: str, n: int) -> tuple[Any, str | None]:
    ctx = context[:60000]
    body = _primary_body_from_study_ctx(ctx)
    keywords = _context_keywords(body or ctx)
    if len(keywords) < 4:
        return None, "Not enough usable note content for study generation."

    if task == "flashcards":
        prompt = (
            f"You are generating exam-preparation flashcards for a student.\n\n"
            f"RULES:\n"
            f"1. Generate EXACTLY {n} flashcards.\n"
            f"2. PRIMARY SOURCE: the notes below. Most cards (~70%) should test concepts directly from the notes.\n"
            f"3. EXTENDED SOURCE: for the remaining ~30%, create cards on closely related concepts from the SAME subject "
            f"   that a student should know for exam preparation — but NEVER drift to unrelated subjects.\n"
            f"4. Mix difficulty: include definitions, conceptual 'why' questions, tricky edge-case cards, "
            f"   and application/problem-solving cards.\n"
            f"5. 'front' = question or prompt. 'back' = concise, correct answer.\n"
            f"6. 'source' = 'notes' if from the notes, 'subject_knowledge' if extended.\n"
            f"7. Output ONLY a JSON array. No markdown fence, no extra text.\n"
            f"   Each object: {{\"front\": str, \"back\": str, \"source\": str}}\n\n"
            f"Notes:\n{ctx}"
        )
    else:
        prompt = (
            f"You are generating challenging exam-style MCQs for a student.\n\n"
            f"RULES:\n"
            f"1. Generate EXACTLY {n} MCQs.\n"
            f"2. PRIMARY SOURCE: the notes below. Most questions (~70%) should test concepts directly from the notes.\n"
            f"3. EXTENDED SOURCE: for the remaining ~30%, create questions on closely related concepts from the SAME subject "
            f"   that commonly appear in exams — but NEVER drift to unrelated subjects.\n"
            f"4. Mix difficulty: include recall, application, analysis, and tricky 'gotcha' questions.\n"
            f"5. Each question must have exactly 4 plausible options. Distractors should be realistic, not obviously wrong.\n"
            f"6. 'source' = 'notes' if from the notes, 'subject_knowledge' if extended.\n"
            f"7. Output ONLY a JSON array. No markdown fence, no extra text.\n"
            f"   Each object: {{\"question\": str, \"options\": [str,str,str,str], \"answer_index\": 0-3, \"source\": str}}\n\n"
            f"Notes:\n{ctx}"
        )

    last_err = "generation failed"
    for k in range(3):
        msg = prompt if k == 0 else (prompt + "\n\nPrevious output had formatting issues. Regenerate valid JSON strictly following the schema.")
        raw, err = _sarvam_chat_complete(
            [{"role": "user", "content": msg}],
            model=_model_study(),
            max_tokens=8192,
            temperature=0.4,
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
                valid = _validate_flashcards_smart(coerced, n)
            else:
                valid = _validate_mcq_smart(coerced, n)
            if valid is not None and len(valid) >= 1:
                return valid, None
            last_err = "Model output did not match expected schema."
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


def topic_summary(context: str) -> tuple[str | None, str | None]:
    """Generate a concise 100-150 word summary covering all key topics from the notes."""
    ctx = (context or "").strip()[:60000]
    body = _primary_body_from_study_ctx(ctx)
    keywords = _context_keywords(body or ctx)
    if len(keywords) < 4:
        return None, "Not enough usable note content for summary generation."

    prompt = (
        "You are a study assistant. Read the student's notes below and write a SINGLE, "
        "concise summary of 100-150 words.\n\n"
        "RULES:\n"
        "1. Cover ALL major topics, definitions, formulas, and key takeaways from the notes.\n"
        "2. Use the same terminology as the notes.\n"
        "3. Structure: start with the overarching topic, then list key concepts in logical order.\n"
        "4. Do NOT add content from outside the notes — summarize only what is provided.\n"
        "5. Write in plain text paragraphs (no bullet points, no JSON, no markdown headers).\n"
        "6. Aim for exactly 100-150 words. Be dense and information-rich.\n\n"
        f"Notes:\n{ctx}"
    )

    last_err = "generation failed"
    for k in range(2):
        msg = prompt if k == 0 else (prompt + "\n\nPrevious output was too long or formatted incorrectly. Write 100-150 words of plain text only.")
        raw, err = _sarvam_chat_complete(
            [{"role": "user", "content": msg}],
            model=_model_study(),
            max_tokens=1024,
            temperature=0.2,
        )
        if err:
            last_err = err
            continue
        if not raw:
            last_err = "empty model output"
            continue
        text = raw.strip()
        text = re.sub(r"^```(?:text)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
        if len(text) >= 40:
            return text, None
        last_err = "summary too short"

    if body and len(body) >= 60:
        sentences = [s.strip() for s in re.split(r"[.!?\n]+", body) if len(s.strip()) > 20]
        fallback = ". ".join(sentences[:8])
        if len(fallback) >= 40:
            return fallback[:600] + ".", None
    return None, last_err


