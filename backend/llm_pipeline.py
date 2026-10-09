"""LLM + OCR: Sarvam AI — Document Intelligence (Sarvam Vision) + Chat Completions."""
import json
import logging
import os
import random
import re
import tempfile
import threading
import zipfile
from typing import Any, Iterator

import httpx

logger = logging.getLogger(__name__)

# Chat models (see https://docs.sarvam.ai/api-reference-docs/api-guides-tutorials/chat-completion/overview)
_DEFAULT_MODEL_RAG = "sarvam-105b"
_DEFAULT_MODEL_STUDY = "sarvam-105b"  # Sarvam retired sarvam-30b in 2026
# Flashcards, quizzes and summaries don't need the model's long reasoning: without it they take
# about a second instead of up to a minute, and cost a fraction of the tokens.
_STUDY_REASONING_EFFORT = "off"

_GROUNDING_STOPWORDS = {
    "about", "after", "again", "also", "among", "another", "because", "before", "being", "between",
    "both", "could", "does", "each", "from", "have", "into", "more", "most", "other", "over", "same",
    "some", "such", "than", "that", "their", "there", "these", "they", "this", "those", "through",
    "under", "using", "very", "what", "when", "where", "which", "while", "with", "would",
}


_usage = threading.local()


def _add_usage(prompt_tokens: int, completion_tokens: int) -> None:
    _usage.prompt = getattr(_usage, "prompt", 0) + int(prompt_tokens or 0)
    _usage.completion = getattr(_usage, "completion", 0) + int(completion_tokens or 0)


def estimate_tokens(text: str) -> int:
    """Rough token count (about 4 characters per token) when the API doesn't report usage."""
    return max(1, len(text or "") // 4)


def take_usage() -> dict:
    """AI tokens used by chat calls made in this thread since the last call (then resets to zero)."""
    out = {"prompt_tokens": getattr(_usage, "prompt", 0), "completion_tokens": getattr(_usage, "completion", 0)}
    _usage.prompt = _usage.completion = 0
    return out


def language_instruction(language: str | None) -> str:
    if not language or language == "English":
        return ""
    return (
        f"Write all of your output in {language}. Keep formulas, code, units and standard technical "
        "terms as they are (English or LaTeX)."
    )


def _sarvam_api_key() -> str | None:
    k = os.getenv("SARVAM_API_KEY")
    return k.strip() if k else None


def _chat_base_url() -> str:
    return os.getenv("SARVAM_API_BASE", "https://api.sarvam.ai").rstrip("/")


def _model_rag() -> str:
    return (os.getenv("SARVAM_MODEL_RAG") or "").strip() or _DEFAULT_MODEL_RAG


def _model_study() -> str:
    return (os.getenv("SARVAM_MODEL_STUDY") or "").strip() or _DEFAULT_MODEL_STUDY


def _sarvam_chat_complete(
    messages: list[dict[str, str]],
    *,
    model: str,
    max_tokens: int,
    temperature: float,
    reasoning_effort: str | None = None,
) -> tuple[str | None, str | None]:
    key = _sarvam_api_key()
    if not key:
        return None, "SARVAM_API_KEY not configured"
    url = f"{_chat_base_url()}/v1/chat/completions"
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
    if reasoning_effort == "off":
        payload["reasoning_effort"] = None  # Sarvam: an explicit null turns reasoning off
    elif reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort  # low | medium (default) | high | max
    try:
        r = httpx.post(
            url,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=300.0,
        )
        data = r.json()
        if r.status_code >= 400:
            err = data.get("error") if isinstance(data.get("error"), dict) else {}
            msg = err.get("message") if isinstance(err, dict) else None
            if "deprecated" in (msg or "").lower() and model != _DEFAULT_MODEL_RAG:
                # A model setting that Sarvam has since retired: keep working on the default model.
                logger.warning("Sarvam model %s is retired (%s); using %s", model, msg, _DEFAULT_MODEL_RAG)
                return _sarvam_chat_complete(
                    messages,
                    model=_DEFAULT_MODEL_RAG,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    reasoning_effort=reasoning_effort,
                )
            return None, msg or r.text or f"HTTP {r.status_code}"
        choices = data.get("choices") or []
        if not choices:
            return None, "no choices in response"
        content = (choices[0].get("message") or {}).get("content")
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        _add_usage(
            usage.get("prompt_tokens") or estimate_tokens(json.dumps(messages)),
            usage.get("completion_tokens") or estimate_tokens(str(content or "")),
        )
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


# Sarvam's markdown embeds every figure it finds as a base64 image: often hundreds of KB per page,
# which would flood search with junk passages. Keep a short marker instead.
_INLINE_IMAGE_RES = (
    re.compile(r"!\[[^\]]*\]\(\s*data:[^)]*\)"),
    re.compile(r"<img\b[^>]*\bsrc\s*=\s*[\"']data:[^\"']*[\"'][^>]*>", re.IGNORECASE),
    re.compile(r"data:image/[\w.+-]+;base64,[A-Za-z0-9+/=\s]{64,}"),
)


def strip_inline_images(text: str) -> str:
    for pattern in _INLINE_IMAGE_RES:
        text = pattern.sub("[figure]", text or "")
    return text


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
        return strip_inline_images(z.read(pick).decode("utf-8", errors="replace")).strip()


def _strings_in(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out = []
        for k in ("text", "content", "markdown"):
            if isinstance(value.get(k), str):
                out.append(value[k])
        for v in value.values():
            if isinstance(v, (dict, list)):
                out.extend(_strings_in(v))
        return out
    if isinstance(value, list):
        return [s for v in value for s in _strings_in(v)]
    return []


def _page_texts_from_output_zip(zip_path: str) -> list[str]:
    """Per-page text from metadata/page_NNN.json files in a Document Intelligence output zip ([] if absent)."""
    pages: list[tuple[int, str]] = []
    try:
        with zipfile.ZipFile(zip_path, "r") as z:
            for name in z.namelist():
                m = re.search(r"page_(\d+)\.json$", name)
                if not m:
                    continue
                try:
                    data = json.loads(z.read(name).decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    continue
                pages.append((int(m.group(1)), strip_inline_images(" ".join(_strings_in(data)))))
    except zipfile.BadZipFile:
        return []
    return [text for _, text in sorted(pages)]


def _document_intelligence(file_bytes: bytes, mime: str) -> tuple[str | None, str | None]:
    text, err, _pages = _document_intelligence_with_pages(file_bytes, mime)
    return text, err


def _document_intelligence_with_pages(file_bytes: bytes, mime: str) -> tuple[str | None, str | None, list[str]]:
    """
    OCR / layout text via Sarvam Document Intelligence (Sarvam Vision) for a PDF or an image.
    See: https://docs.sarvam.ai/api-reference-docs/api-guides-tutorials/document-intelligence/overview
    """
    key = _sarvam_api_key()
    if not key:
        return None, "SARVAM_API_KEY not configured", []
    try:
        from sarvamai import SarvamAI
    except ImportError:
        return None, "sarvamai package not installed (pip install sarvamai)", []

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
            return None, f"Document intelligence job state: {state or 'unknown'}", []
        zfd, tmp_zip = tempfile.mkstemp(suffix=".zip")
        os.close(zfd)
        job.download_output(tmp_zip)
        text = _extract_markdown_from_output_zip(tmp_zip)
        if not text:
            return None, "empty document intelligence output", []
        return text, None, _page_texts_from_output_zip(tmp_zip)
    except Exception as e:
        logger.warning("Sarvam document intelligence failed: %s", e)
        return None, str(e), []
    finally:
        for tmp in (tmp_file, tmp_zip):
            if tmp and os.path.isfile(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass


def transcribe_handwritten_image(image_bytes: bytes, mime: str = "image/jpeg") -> tuple[str | None, str | None]:
    """Sarvam Vision (Document Intelligence) → markdown/plain text from note images."""
    return _document_intelligence(image_bytes, mime or "image/jpeg")


def transcribe_document_bytes(file_bytes: bytes, mime: str = "application/pdf") -> tuple[str | None, str | None]:
    """Sarvam Vision (Document Intelligence) OCR for uploaded PDFs/images."""
    return _document_intelligence(file_bytes, mime)


def transcribe_pdf_with_pages(file_bytes: bytes) -> tuple[str | None, str | None, list[str]]:
    """Like transcribe_document_bytes for a PDF, plus each page's own text (when Sarvam provides it)."""
    return _document_intelligence_with_pages(file_bytes, "application/pdf")


def _rag_messages(question: str, context: str, language: str = "English") -> list[dict[str, str]]:
    ctx = (context or "").strip()[:60000]
    q = (question or "").strip()
    lang = language_instruction(language)
    messages = [
        {
            "role": "system",
            "content": (
                "You are a brilliant subject-matter tutor helping a student prepare for exams.\n\n"
                "GROUNDING RULES:\n"
                "- The student's notes are your PRIMARY source of truth.\n"
                "- Identify the subject/topic from the notes (e.g. Machine Learning, Linear Algebra, Operating Systems).\n"
                "- ALWAYS answer the student's question — never refuse or say 'not in notes'.\n"
                "- When the notes contain the answer, cite specific details from them.\n"
                "- Excerpts marked (main source) come from files the student marked as most reliable; prefer them.\n"
                "- When the notes are incomplete, supplement with correct, textbook-level knowledge for THAT subject.\n"
                "  Mark such additions with '📖 Beyond notes:' so the student knows.\n"
                "- NEVER drift to unrelated subjects. If asked about something outside the course scope, "
                "  briefly redirect: 'That's outside [subject]. Here's what your notes cover instead…'\n\n"
                "TEACHING STYLE:\n"
                "- Explain like a great tutor: clear, concise, with examples.\n"
                "- For problem-solving: show step-by-step working.\n"
                "- For tricky exam practice: generate 5-8 challenging questions with concise answer keys.\n"
                "- Use Markdown: bullet points, short paragraphs, tables when useful.\n"
                "- Write math in LaTeX: $...$ inline and $$...$$ for display equations.\n"
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
    if lang:
        messages[0]["content"] += "\n- " + lang
    return messages


def sarvam_rag_answer(question: str, context: str, language: str = "English") -> tuple[str | None, str | None]:
    """Answer from notes-first context with constrained subject verification."""
    if not (question or "").strip():
        return None, "Empty query"
    return _sarvam_chat_complete(
        _rag_messages(question, context, language),
        model=_model_rag(),
        max_tokens=4096,
        temperature=0.3,
    )


class LLMError(Exception):
    """The AI service failed; the message is safe to log, not meant for students."""


def sarvam_rag_answer_stream(question: str, context: str, language: str = "English") -> Iterator[str]:
    """
    Same answer as sarvam_rag_answer, yielded piece by piece as the model writes it
    (server-sent events, `stream: true`). Raises LLMError if the request fails.
    """
    key = _sarvam_api_key()
    if not key:
        raise LLMError("SARVAM_API_KEY not configured")
    url = f"{_chat_base_url()}/v1/chat/completions"
    payload = {
        "model": _model_rag(),
        "messages": _rag_messages(question, context, language),
        "max_tokens": 4096,
        "temperature": 0.3,
        "stream": True,
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    try:
        with httpx.stream("POST", url, headers=headers, json=payload, timeout=300.0) as r:
            if r.status_code >= 400:
                r.read()
                raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}")
            for line in r.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                for choice in chunk.get("choices") or []:
                    piece = (choice.get("delta") or {}).get("content")
                    if piece:
                        yield piece
    except httpx.HTTPError as e:
        raise LLMError(str(e)) from e


# Models often write LaTeX inside JSON strings with single backslashes: "\cdot" is an invalid JSON
# escape (the whole reply fails to parse), and "\frac" / "\theta" / "\neq" parse silently as a form
# feed / tab / newline. A backslash followed by two or more lowercase letters is a LaTeX command (a
# real line break is "\n" before a capital, digit or space), so it is escaped before parsing.
_LATEX_COMMAND = re.compile(r"(?<!\\)\\(?=[a-z]{2,})")
_BAD_JSON_ESCAPE = re.compile(r'(?<!\\)\\(?![\\"/bfnrtu])')


def _repair_latex_escapes(raw: str) -> str:
    return _BAD_JSON_ESCAPE.sub(r"\\\\", _LATEX_COMMAND.sub(r"\\\\", raw))


def _parse_json_loose(raw: str) -> Any:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```\s*$", "", raw)
    return json.loads(_repair_latex_escapes(raw))


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
        explanation = str(it.get("explanation") or "").strip()
        order = list(range(4))
        random.shuffle(order)
        cleaned.append({
            "question": q,
            "options": [norm_opts[i] for i in order],
            "answer_index": order.index(ai_i),
            "explanation": explanation,
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


def cheap_study_json(context: str, task: str, n: int, language: str = "English") -> tuple[Any, str | None]:
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
            f"7. Write math in LaTeX ($...$). Output ONLY a JSON array. No markdown fence, no extra text.\n"
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
            f"6. 'explanation' = one or two sentences on why the correct option is right (and the tempting wrong one is wrong).\n"
            f"7. 'source' = 'notes' if from the notes, 'subject_knowledge' if extended.\n"
            f"8. Write math in LaTeX ($...$). Output ONLY a JSON array. No markdown fence, no extra text.\n"
            f"   Each object: {{\"question\": str, \"options\": [str,str,str,str], \"answer_index\": 0-3, \"explanation\": str, \"source\": str}}\n\n"
            f"Notes:\n{ctx}"
        )

    lang = language_instruction(language)
    if lang:
        prompt = prompt.replace("\n\nNotes:\n", f"\n\n{lang} JSON keys stay in English.\n\nNotes:\n", 1)
    label = "flashcards" if task == "flashcards" else "MCQs"
    last_err = "generation failed"
    for k in range(3):
        msg = prompt if k == 0 else (prompt + "\n\nPrevious output had formatting issues. Regenerate valid JSON strictly following the schema.")
        raw, err = _sarvam_chat_complete(
            [{"role": "user", "content": msg}],
            model=_model_study(),
            max_tokens=8192,
            temperature=0.4,
            reasoning_effort=_STUDY_REASONING_EFFORT,
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

    # Never invent study items ourselves: a wrong "answer key" is worse than a clear error.
    logger.warning("Study generation (%s) failed after retries: %s", task, last_err)
    return None, f"The AI couldn't generate {label} right now. Please try again."


def topic_summary(context: str, language: str = "English") -> tuple[str | None, str | None]:
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
        "6. Aim for exactly 100-150 words. Be dense and information-rich.\n"
        f"{language_instruction(language)}\n\n"
        f"Notes:\n{ctx}"
    )

    last_err = "generation failed"
    for k in range(2):
        msg = prompt if k == 0 else (prompt + "\n\nPrevious output was too long or formatted incorrectly. Write 100-150 words of plain text only.")
        raw, err = _sarvam_chat_complete(
            [{"role": "user", "content": msg}],
            model=_model_study(),
            max_tokens=4096,  # room for the model's reasoning as well as the ~150-word summary
            temperature=0.2,
            reasoning_effort=_STUDY_REASONING_EFFORT,
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

    logger.warning("Summary generation failed after retries: %s", last_err)
    return None, "The AI couldn't write a summary right now. Please try again."


