# NoteScanner

NoteScanner is a FastAPI + React platform for uploading notes, extracting text, indexing with embeddings, and generating grounded learning outputs.

Core capabilities:

- Upload PDFs/images/text and extract text with Vision OCR.
- Store persistent note chunks + embeddings in ChromaDB for retrieval.
- Support temporary chat-only uploads from the chatbot `+` button (ephemeral cache, no DB persistence).
- Answer questions and generate flashcards/MCQs/mind maps from retrieved context.

NoteScanner uploads course materials, indexes them in ChromaDB, and uses **Sarvam AI**: **Document Intelligence (Sarvam Vision)** for PDF/image OCR and **Chat Completions** (`sarvam-30b` / `sarvam-105b`) for note-grounded Q&A, flashcards, MCQs, and mind maps. Tesseract remains a fallback extractor. It features a FastAPI backend and a React/Vite frontend, orchestrated with Docker Compose.

You upload your handwritten notes → the system scans, organizes, and lets you chat with your notes using Retrieval-Augmented Generation (RAG).

NoteScanner Demo

## Prerequisites

- Docker & Docker Compose
- Python 3.10+
- Node.js 20+

## System Architecture

- `frontend/my-app`: React + Vite UI
- `backend/api.py`: FastAPI API layer
- `backend/chroma_store.py`: Chroma client + collection accessors
- `backend/ingest_api.py`: chunking + embedding ingestion
- `backend/llm_pipeline.py`: OCR + LLM calls
- `backend/vfs_tree.py`: virtual folder tree helpers
- `backend/file_meta.py`: file metadata policy layer

## End-to-End Pipeline

### 1) Persistent note upload pipeline (Explorer)

Endpoint: `POST /upload_note`

Flow:

1. Client uploads file to backend.
2. Backend reads file bytes in memory.
3. Text extraction:
  - PDF: Sarvam Document Intelligence first, parser fallback.
  - Image: Sarvam Vision OCR first, Tesseract fallback.
  - Plain text formats: UTF-8 decode.
4. Backend persists:
  - full extracted text in `user_documents`,
  - chunked text + embeddings in `user_{user_id}_notes`.
5. VFS is updated for explorer visibility.

### 2) Ephemeral chatbot `+` upload pipeline

Endpoint: `POST /chat/upload_ephemeral`

Flow:

1. Frontend sends file with `chat_session_id`.
2. Backend extracts text in memory.
3. Backend stores extracted text in process memory cache keyed by `(user_id, chat_session_id)`.

Important:

- No `user_documents` write.
- No `user_{user_id}_notes` write.
- No Chroma persistence for this path.

Cache clear:

- `POST /chat/session/clear`
- Triggered by chat refresh/new chat/unmount.

### 3) Query retrieval sequence (implemented priority)

Endpoint: `POST /query_folder`

Retrieval order:

1. Ephemeral chat uploads (`+` cache)
2. Opened file in viewer
3. Other files in same course folder

Selection logic:

- Embedding-based relevance scoring is computed per stage.
- The highest-priority relevant stage is selected.
- If relevance is weak at a higher stage, system falls to next stage.
- This reduces unrelated-domain leakage (for example RL queries answered from generic ML notes).

### 4) Study generation pipeline

Endpoints:

- `POST /study/generate` (task: `flashcards` or `mcq`)
- `POST /study/mindmap`

Context policy:

- Opened file is primary context.
- Same-course files can be included as supporting context.

## API Reference (Main Endpoints)

### Authentication

- `POST /register`
- `POST /login`
- `GET /me`
- `GET /guest_id`

### Notes and VFS

- `GET /list_tree`
- `POST /create_folder`
- `POST /create_file`
- `POST /upload_note`
- `POST /move_path`
- `POST /delete_path`
- `DELETE /notes`
- `GET /file_meta`
- `POST /file_meta`

### Query and chat uploads

- `POST /query_folder`
- `POST /chat/upload_ephemeral`
- `POST /chat/session/clear`

### Study tools

- `POST /study/generate`
- `POST /study/mindmap`

### OneNote integration

- `GET /integrations/onenote/status`
- `GET /integrations/onenote/auth_url`
- `GET /integrations/onenote/callback`
- `POST /integrations/onenote/sync`

## Database and Collection Design

Chroma is used as central storage for both metadata and vector retrieval.

Collections:

- `users`: email, hashed password, name
- `sessions`: session_id -> user_id
- `user_documents`: full extracted text per logical path
- `user_{user_id}_notes`: chunk docs + embeddings + chunk metadata
- `user_{user_id}_vfs`: virtual tree + file meta JSON
- `onenote_tokens`: OAuth tokens
- `onenote_oauth_states`: OAuth state for callback

Notes:

- Guest mode uses effective IDs like `guest_<uuid>`.
- Persistent note uploads are stored/indexed.
- Chatbot `+` uploads are intentionally ephemeral.

## LLM and OCR Implementation

From `backend/llm_pipeline.py`:

- OCR:
  - Sarvam Document Intelligence / Vision for PDF-image text extraction.
  - Local fallback extractors where needed.
- Q&A:
  - `sarvam_rag_answer(question, context)`
- Study generation:
  - `cheap_study_json(context, task, n)` for flashcards/MCQ
  - `mind_map_json(context)` for mind maps

Model env controls:

- `SARVAM_MODEL_RAG` (default: `sarvam-105b`)
- `SARVAM_MODEL_STUDY` (default: `sarvam-30b`)

## Configuration

Required:

- `SARVAM_API_KEY`

Optional Sarvam:

- `SARVAM_API_BASE` (default `https://api.sarvam.ai`)
- `SARVAM_MODEL_RAG`
- `SARVAM_MODEL_STUDY`
- `SARVAM_DOC_INTEL_LANGUAGE` (default `en-IN`)
- `SARVAM_DOC_INTEL_TIMEOUT` (default `180`)

Chroma options:

- Cloud:
  - `CHROMA_API_KEY`
  - `CHROMA_TENANT`
  - `CHROMA_DATABASE`
- Self-hosted:
  - `CHROMA_HOST`
  - `CHROMA_PORT`
  - `CHROMA_SSL` (optional)

Other:

- `USER_DOCUMENT_MAX_BYTES`
- `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`, `MICROSOFT_REDIRECT_URI` (OneNote)

## Run with Docker Compose

```bash
docker compose up --build
```

Services:

- Backend: `http://localhost:8000`
- Frontend: `http://localhost:5173`

Rebuild backend after backend changes:

```bash
docker compose build backend && docker compose up -d backend
```

## Local Development (No Compose)

Backend:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn backend.api:app --host 0.0.0.0 --port 8000
```

Frontend:

```bash
cd frontend/my-app
npm install
npm run dev -- --host
```

## Current Product Rules

- Chatbot `+` uploads are temporary per chat session.
- Temporary uploads are not persisted in Chroma collections.
- Query routing order is: chat cache -> open file -> same-course files.
- Chat refresh/new chat clears temporary cache.

## Repository Layout

- `backend/` API, ingestion, retrieval, auth, VFS, LLM integration
- `frontend/my-app/` UI and interaction layer
- `docker-compose.yml` local orchestration
- `requirements.txt` backend dependencies

