# NoteScanner

## Overview
NoteScanner is an end-to-end pipeline for uploading, extracting, embedding, and querying notes using OCR, ChromaDB, and Groq LLM. It features a FastAPI backend and a React/Vite frontend, orchestrated with Docker Compose.

You upload your handwritten notes → the system scans, organizes, and lets you chat with your notes using Retrieval-Augmented Generation (RAG).

![NoteScanner Demo](frontend/my-app/public/app-page.png)

## Prerequisites
- Docker & Docker Compose
- Python 3.10+
- Node.js 20+
## Quick Start (Recommended: Docker Compose)

1. **Clone the repository:**
	```sh
	git clone https://github.com/cherie-dips/NoteScanner
	cd NoteScanner
	```

2. **Set up environment variables** (in `.env` at repo root):
	- `GROQ_API_KEY` – for AI query (required for RAG).
	- All data (user accounts, sessions, extracted text, RAG embeddings) is stored in **ChromaDB** under `user_notes/` (no MongoDB).

3. **Build and run all services:**
	```sh
	docker-compose up --build
	```
	- Backend: http://localhost:8000
	- Frontend: http://localhost:5173

## Manual Setup (Dev Mode)

### Backend
1. Create and activate a Python virtual environment:
	```sh
	python3 -m venv venv
	source venv/bin/activate
	pip install -r requirements.txt
	```
2. Set up `.env` with your API keys (e.g. `GROQ_API_KEY`).
3. Start the backend (no separate database service needed; ChromaDB stores everything under `user_notes/`):
	```sh
	uvicorn backend.api:app --host 0.0.0.0 --port 8000
	```

### Frontend
1. Install dependencies:
	```sh
	cd frontend/my-app
	npm install
	```
2. Start the frontend:
	```sh
	npm run dev -- --host
	```
3. Access at http://localhost:5173

## Authentication and data (ChromaDB only)
- **Single database:** User accounts, sessions, and extracted text are stored in **ChromaDB** (no MongoDB).
- **Paths:** All ChromaDB data lives under the notes directory:
  - `user_notes/_chroma/` – global store (users, sessions, extracted text per file).
  - `user_notes/<user_id>/` – uploaded PDFs and images plus `.txt` extracted files.
  - `user_notes/<user_id>/chroma/` – RAG embeddings for that user (one collection per folder/subject).
- **Sign up / Sign in:** Email and hashed password are stored in ChromaDB; session IDs are used for auth.

## Workflow
1. **Sign up or sign in** on the frontend.
2. **Create folders and upload notes (PDFs/images).** Files are saved under `user_notes/<user_id>/`; extracted text is stored in ChromaDB and as `.txt` next to the originals.
3. **Backend extracts text** (OCR for images, PyMuPDF for PDFs), saves `.txt` locally and the full text in ChromaDB.
4. **Query your notes** by subject; RAG uses the ChromaDB collections under `user_notes/<user_id>/chroma/`.

## Troubleshooting

### "Could not connect to server. Is the backend running?"

The frontend talks to the backend at `http://localhost:8000` in dev. Fix it by:

1. **Start the backend first** (from the repo root):
   ```sh
   source venv/bin/activate   # or create one: python3 -m venv venv && source venv/bin/activate
   pip install -r requirements.txt
   uvicorn backend.api:app --host 0.0.0.0 --port 8000
   ```
2. **Then start the frontend** (in another terminal):
   ```sh
   cd frontend/my-app && npm install && npm run dev -- --host
   ```
3. Open **http://localhost:5173**. The frontend will call `http://localhost:8000` for `/guest_id`, `/upload_note`, `/register`, etc.

If the backend runs on another host or port, set `VITE_API_URL` in `frontend/my-app/.env` (e.g. `VITE_API_URL=http://192.168.1.5:8000`) and restart the dev server.

### "Registration failed" or "Database unavailable"

- All data is stored in ChromaDB under `user_notes/`. Ensure the backend can create and write to `user_notes/_chroma` and `user_notes/<user_id>/chroma`.
- If you see **"Registration failed"** with no other message: check the terminal where `uvicorn` is running for the real error.

## Useful Commands
- **Rebuild containers** (required after backend code changes, e.g. new routes like delete):
  ```sh
  docker-compose build backend && docker-compose up -d
  # or: docker-compose up --build
  ```
- **Stop containers:**
  ```sh
  docker-compose down
  ```

## Deploy to GitHub Pages

The frontend can be deployed to **https://&lt;username&gt;.github.io/NoteScanner/**.

1. **Enable GitHub Pages (one-time):**
   - In your repo: **Settings → Pages**
   - Under **Build and deployment**, set **Source** to **GitHub Actions**.

2. **Deploy:**
   - Push to the `main` branch (or run the workflow from the **Actions** tab).
   - The **Deploy to GitHub Pages** workflow will build the frontend and deploy it.
   - After it finishes, the site will be live at `https://<username>.github.io/NoteScanner/`.

3. **Backend for the deployed site:**
   - GitHub Pages only serves the static frontend. For explorer, uploads, and AI query to work, the backend must be running and reachable.
   - Either run the backend locally and use the same machine to open the GitHub Pages URL, or deploy the backend (e.g. Railway, Render) and set `VITE_API_URL` in the workflow to your backend URL before building (e.g. add an env var in the “Install and build” step).

## File Structure
- `backend/` - FastAPI endpoints, extraction, ingestion, query logic
- `frontend/my-app/` - React/Vite frontend
- `user_notes/` - Uploaded notes and extracted text (ignored in git)
- `chroma_storage/` - ChromaDB vector database (ignored in git)
- `.env` - Secrets and API keys (ignored in git)

## Future Steps
- Adding a functionality to perform OCR on handwritten PDF documents. 