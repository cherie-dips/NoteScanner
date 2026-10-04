# NoteScanner

NoteScanner turns your course notes (PDFs, photos of handwritten pages, text files, OneNote pages)
into a study assistant: ask questions answered from your own notes, generate flashcards and
practice MCQs, and review saved flashcards on a spaced-repetition schedule.

![NoteScanner](frontend/my-app/public/app-page.png)

It uses **Sarvam AI**: Document Intelligence (Sarvam Vision) to read PDFs and images, and Chat
Completions (`sarvam-105b` / `sarvam-30b`) for answers and study material. Tesseract and PyMuPDF are
local fallbacks for reading files. Notes are stored and searched in **ChromaDB** (Chroma Cloud or
self-hosted). Backend: FastAPI. Frontend: React + Vite.

## Features

- **Notes explorer**: folders, upload (many files at once, drag and drop), rename, move, delete,
  "main source" marking. Uploads are read in the background with progress shown.
- **Ask your notes**: answers stream in as they're written, with clickable sources (with page numbers
  for PDFs). Search order: files attached to the chat (+) → the open file → the same course folder →
  all other notes. Select text in a file and choose "Ask about this" to ask about that passage.
  Answers can be in English or one of 10 Indian languages (Account settings).
- **View and fix text**: see the text NoteScanner read from a file and correct reading mistakes, or
  write notes directly into a new file.
- **Study tools**: flashcards (optionally made automatically for each upload), MCQs with
  explanations and a score, short summaries.
- **Spaced repetition**: save flashcards as decks and review what's due (Again / Hard / Good / Easy).
  Export decks as CSV for Anki. Optional daily "cards due" reminder email.
- **Today dashboard**: cards due, streak, weak topics (from quiz results), 14-day activity.
- **Exam mode**: a day-by-day revision plan up to an exam date, and timed mock tests drawn from a
  whole course folder.
- **Accounts**: sign-up, sign-in, sessions that expire, change password, password reset by email
  (when SMTP is configured), delete account.
- **OneNote import** (when Microsoft OAuth is configured).
- **For running a pilot**: a privacy note accepted at sign-up, a "Getting started" folder for new
  accounts, 👍/👎 answer ratings and a feedback form, an admin page (usage, estimated AI cost,
  feedback), an optional monthly AI budget, error alerts to Slack/Discord, encrypted weekly backups,
  an uptime check, and an answer-quality test (`scripts/answer_quality.py`).
- Math is rendered with KaTeX; works on phones (one pane at a time with a bottom tab bar) and can be
  installed as an app (PWA).

## Architecture

- `frontend/my-app/`: React + Vite UI
- `backend/api.py`: app setup, error handling, CORS, `/health`, `/config`
- `backend/routes/`: HTTP routes by feature: `account.py`, `files.py`, `chat.py`, `study.py`, `onenote.py`, `admin.py`
- `backend/settings.py`: environment settings, request limits, search thresholds
- `backend/chroma_store.py`: all Chroma reads/writes (paged and batched to fit Chroma Cloud limits)
- `backend/uploads.py`: reading text out of uploads + the background upload queue
- `backend/ingest_api.py`: chunking + embeddings (`all-MiniLM-L6-v2`, loaded once)
- `backend/search.py`: finding the note excerpts that answer a question
- `backend/llm_pipeline.py`: Sarvam OCR, answers (normal + streaming), flashcards/MCQs/summaries
- `backend/study_store.py`: saved decks and the spaced-repetition schedule
- `backend/chat_cache.py`: temporary chat uploads (memory only)
- `backend/auth.py`, `backend/deps.py`: passwords, sessions, who is calling
- `backend/mailer.py`: password-reset emails over SMTP
- `backend/vfs_tree.py`, `backend/paths.py`, `backend/file_meta.py`: folder tree and file metadata
- `backend/legacy_migration.py`: one-time move of data saved by older versions
- `backend/usage.py`: per-student daily counts (questions, uploads, pages, tokens, reviews), cost, budget
- `backend/preferences.py`, `backend/reminders.py`: answer language, daily reminder emails
- `backend/feedback.py`, `backend/alerts.py`: answer ratings/feedback, error alerts to a webhook
- `backend/pages.py`: page numbers for PDF text; `backend/starter_notes.py`: notes for new accounts
- `scripts/`: `backup_chroma.py`, `restore_chroma.py`, `answer_quality.py` (see DEPLOYMENT.md, `evals/README.md`)
- `tests/`: pytest suite (runs against an in-memory Chroma that enforces Chroma Cloud's limits)

## How it works

### Uploading notes

`POST /upload_note` checks the file (type, size up to `MAX_UPLOAD_MB`, target folder), queues it,
and returns `202` with a `job_id`. A background worker then:

1. Reads the text: PDF → Sarvam Vision, then PyMuPDF; image → Sarvam Vision, then Tesseract;
   `.txt/.md/.csv/.json/.log` → decoded as UTF-8. Other types are refused (`415`).
2. Saves search chunks + embeddings in `user_{user_id}_notes` and the full text in
   `user_documents`, always under the file's real path.
3. Only then adds the file to the folder tree. Failed jobs leave nothing behind.

The browser polls `GET /jobs/{job_id}` (or `GET /jobs`) to show progress.

### Asking questions

`POST /query_folder/stream` (or `/query_folder` without streaming) searches in this order and uses
the first stage that is relevant enough (`MIN_SCORE_*` in `backend/settings.py`), otherwise the
best-scoring one. Every stage is scored as cosine similarity, so they can be compared fairly.

1. Files attached to this chat with `+` (kept in memory for 2 hours, never stored)
2. The open file
3. Other files in the same top-level course folder
4. All other notes

Files marked as a "main source" get a small ranking boost and are labelled for the AI.
The streaming response is newline-delimited JSON: one `meta` line with the sources, `delta` lines
with answer text, then `done` (or `error`).

### Study tools

- `POST /study/generate` (`task=flashcards|mcq`) and `POST /study/summary` use the open file plus
  same-course files. If the AI fails after retries, the user gets an error. The app never invents
  study items itself.
- Decks: `POST /study/decks`, `GET /study/decks`, `GET /study/decks/{id}/cards?due_only=true`,
  `POST /study/cards/{id}/review` (`grade=again|hard|good|easy`), `DELETE /study/decks/{id}`,
  `GET /study/decks/{id}/export` (CSV for Anki). Scheduling is a simplified SM-2.

## API reference

| Area | Routes |
|---|---|
| Accounts | `POST /register`, `POST /login`, `POST /logout`, `GET /me`, `GET /guest_id`, `POST /account/change_password`, `POST /account/delete`, `POST /password/forgot`, `POST /password/reset` |
| Files | `GET /list_tree`, `POST /create_folder`, `POST /create_file`, `POST /upload_note`, `GET /jobs`, `GET /jobs/{job_id}`, `POST /move_path`, `POST /rename_path`, `POST /delete_path`, `GET /file_text`, `POST /file_text`, `GET /file_meta`, `POST /file_meta`, `DELETE /notes` |
| Chat | `POST /query_folder`, `POST /query_folder/stream`, `POST /chat/upload_ephemeral`, `POST /chat/session/clear` |
| Study | `POST /study/generate`, `POST /study/summary` (old name `/study/mindmap` still works), deck routes above, `GET /study/dashboard`, `POST /study/mcq_results`, `POST /study/exam`, `POST /study/exam_plan`, `GET /study/exams`, `DELETE /study/exams/{id}` |
| Pilot | `GET/POST /account/preferences`, `POST /feedback`, `GET /admin/stats`, `GET /admin/feedback` (admins only) |
| OneNote | `GET /integrations/onenote/status`, `GET /integrations/onenote/auth_url`, `GET /integrations/onenote/callback`, `POST /integrations/onenote/sync` |
| Service | `GET /health`, `GET /config` |

Auth is the `X-Session-Id` header. Sessions expire after `SESSION_TTL_DAYS` (default 14); an expired
or ended session gets `401`. Everything that stores data or calls the AI requires a signed-in user.
Interactive docs are at `/docs` when the backend is running.

## Storage (Chroma collections)

- `users`, `sessions`, `password_resets` (only a hash of each reset token is stored)
- `user_documents`: full text per file, split into parts
- `user_{user_id}_notes`: search chunks + embeddings + metadata (`path`, `course_id`, `chunk_index`, …)
- `user_{user_id}_vfs`: folder tree and file metadata (JSON, split into parts)
- `user_{user_id}_study`: decks and cards with their review schedule, quiz results per file, exam plans
- `usage_daily`: one record per student per day with usage counts; `feedback`: ratings and feedback
- `onenote_tokens`, `onenote_oauth_states`

Chroma Cloud allows at most 300 records per read or write, 16 KB per document and 128-byte ids.
`chroma_store.py` pages reads, batches writes, splits large documents into parts, and uses short
hashed ids, so large libraries and long file names work. Older data stays readable.

## Configuration

Required: `SARVAM_API_KEY`, plus Chroma: `CHROMA_API_KEY`, `CHROMA_TENANT`, `CHROMA_DATABASE`
(Cloud) or `CHROMA_HOST`, `CHROMA_PORT`, `CHROMA_SSL` (self-hosted).

Optional:

- Sarvam: `SARVAM_API_BASE`, `SARVAM_MODEL_RAG` (default `sarvam-105b`), `SARVAM_MODEL_STUDY`
  (default `sarvam-30b`), `SARVAM_DOC_INTEL_LANGUAGE` (default `en-IN`), `SARVAM_DOC_INTEL_TIMEOUT` (default `180`)
- Security and limits: `CORS_ORIGINS` (default: localhost dev ports + `https://cherie-dips.github.io`),
  `SESSION_TTL_DAYS` (14), `MAX_UPLOAD_MB` (20), `MAX_CHAT_UPLOAD_CHARS` (300000),
  `CHAT_CACHE_TTL_SECONDS` (7200), `USER_DOCUMENT_MAX_BYTES` (2000000), `RATE_LIMIT_MULTIPLIER` (1;
  scales every request limit, see below).
- Password-reset email: `FRONTEND_URL` (default `https://cherie-dips.github.io/NoteScanner/`),
  `SMTP_HOST`, `SMTP_PORT` (587), `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_STARTTLS` (true).
  Reset is enabled only when `SMTP_HOST` and `SMTP_FROM` are set.
- OneNote: `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`, `MICROSOFT_REDIRECT_URI`
- Pilot: `ADMIN_EMAILS`, `ALERT_WEBHOOK_URL`, `AI_PRICE_PER_PAGE`, `AI_PRICE_PER_1K_INPUT_TOKENS`,
  `AI_PRICE_PER_1K_OUTPUT_TOKENS`, `AI_PRICE_CURRENCY` (INR), `AI_MONTHLY_BUDGET` (0 = off),
  `APP_TIMEZONE` (Asia/Kolkata), `REMINDER_HOUR` (8), `REMINDERS_ENABLED` (true), `STARTER_NOTES` (true).
  See DEPLOYMENT.md.

Run the backend as a **single process** (one uvicorn worker): upload jobs, the chat-upload cache,
request limits and per-user locks live in process memory.

## Request limits

Limits protect logins from password guessing and keep AI costs predictable. They're the `LIMIT_*`
values in `backend/settings.py`; `RATE_LIMIT_MULTIPLIER` raises or lowers all of them at once.

| What | Limit |
|---|---|
| Any request | 1200 / minute per network address, 300 / minute per signed-in browser (`/health` is never limited) |
| Sign-up / login | 50 sign-ups per hour per address; 10 login attempts per 15 min per email and address |
| Password checks, forgot / reset password | 10 per 15 min per user; 3 reset emails per hour per email; 20 reset attempts per hour per address |
| Uploads (notes and chat attachments) | 30 per 10 min and **200 per day** per user |
| Questions | 60 per 10 min and **300 per day** per user |
| Flashcards / MCQs / summaries | 20 per 10 min and **100 per day** per user |
| Folder changes, saving edited text, decks | 120 per minute; 60 per 10 min; 60 per 10 min per user |
| Delete all notes, OneNote connect / sync | 5 per hour; 10 and 5 per 10 min per user |

Going over a limit returns HTTP `429` with a `Retry-After` header and a message such as
"Too many requests. Please try again in 3 minutes" or "You've reached the daily limit of 300
questions…". Counters are kept in memory: they reset when the server restarts. Network addresses come
from `X-Forwarded-For` (set by the hosting proxy), which a client can fake, so the limits that
protect AI costs are counted per account, not per address.

## Running locally

Docker Compose (backend on `:8000`, frontend on `:5173`, local Chroma on `:8100`):

```bash
docker compose up --build
```

Without Docker:

```bash
python3.11 -m venv venv && source venv/bin/activate
pip install -r requirements-dev.txt
uvicorn backend.api:app --host 0.0.0.0 --port 8000

cd frontend/my-app && npm ci && npm run dev -- --host
```

## Tests and checks

```bash
python -m pytest -q tests            # backend (no network or AI key needed; Tesseract required)
cd frontend/my-app && npx eslint . && npm run build
```

CI (`.github/workflows/ci.yml`) runs both on every push and pull request. Deployment is described
in [DEPLOYMENT.md](DEPLOYMENT.md); the plan for getting real students using it is in
[ROADMAP.md](ROADMAP.md).
