# NoteScanner

## Overview
NoteScanner is an end-to-end pipeline for uploading, extracting, embedding, and querying notes using OCR, ChromaDB, and Groq LLM. It features a FastAPI backend and a React/Vite frontend, orchestrated with Docker Compose.

![NoteScanner Demo](noteScanner.png)

## Prerequisites
- Docker & Docker Compose
- Python 3.10+
- Node.js 20+
- (Optional) Redis for advanced vector search

## Quick Start (Recommended: Docker Compose)

1. **Clone the repository:**
	```sh
	git clone <your-repo-url>
	cd NoteScanner
	```

2. **Set up environment variables:**
	- Add your `GROQ_API_KEY`.

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
2. Set up `.env` with your API keys.
3. Start the backend:
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

## Workflow
1. **Create folders and upload notes (PDFs/images) via frontend.**
2. **Backend extracts text (OCR for images, PyMuPDF for PDFs) and saves `.txt` files.**
3. **Extracted text is chunked and embedded using SentenceTransformer, stored in ChromaDB.**
4. **Query your notes by subject and question.**
	- Backend retrieves relevant chunks and uses Groq LLM to answer.

## Useful Commands
- **Rebuild containers:**
  ```sh
  docker-compose up --build
  ```
- **Stop containers:**
  ```sh
  docker-compose down
  ```

## File Structure
- `backend/` - FastAPI endpoints, extraction, ingestion, query logic
- `frontend/my-app/` - React/Vite frontend
- `user_notes/` - Uploaded notes and extracted text (ignored in git)
- `chroma_storage/` - ChromaDB vector database (ignored in git)
- `.env` - Secrets and API keys (ignored in git)
