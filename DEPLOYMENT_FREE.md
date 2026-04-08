# Free Deployment Checklist

This project is set up for:

- Frontend: GitHub Pages
- Backend: Hugging Face Spaces (Docker)

## 1) Deploy backend (Hugging Face Space)

1. Open your Space: `diptidhawade/NoteScanner`.
2. In **Files**, upload this repository content (or push from git).
3. Ensure the Space uses the root `Dockerfile` (already configured to run on `${PORT}` with default `7860`).
4. In Space **Settings -> Variables and secrets**, add:

### Required Space secrets

- `SARVAM_API_KEY`

### Recommended Space variables/secrets

- `SARVAM_API_BASE` (optional, default works)
- `SARVAM_MODEL_RAG` (optional)
- `SARVAM_MODEL_STUDY` (optional)
- `SARVAM_DOC_INTEL_LANGUAGE` (optional)
- `SARVAM_DOC_INTEL_TIMEOUT` (optional)

### Chroma (required for persistent note features)

Use one option:

- Chroma Cloud:
  - `CHROMA_API_KEY`
  - `CHROMA_TENANT`
  - `CHROMA_DATABASE`
- OR self-hosted Chroma:
  - `CHROMA_HOST`
  - `CHROMA_PORT`
  - `CHROMA_SSL` (optional)

1. Wait for Space build to finish.
2. Copy backend URL, e.g. `https://diptidhawade-NoteScanner.hf.space`

## 2) Configure GitHub Pages frontend

1. In your GitHub repo, go to **Settings -> Secrets and variables -> Actions -> New repository secret**.
2. Add this secret:

- `VITE_API_URL` = `https://diptidhawade-NoteScanner.hf.space`

1. Push to `main` (or rerun workflow `Deploy to GitHub Pages`).
2. GitHub Pages URL will be:
  - `https://<your-username>.github.io/NoteScanner/`

## 3) Verify

1. Open frontend URL.
2. Confirm these work:
  - `+` chat upload
  - query response
  - explorer upload
  - study generation

## Notes

- Backend on free HF Space may sleep when idle (cold start delay on first request).
- If frontend shows backend/network errors, first check `VITE_API_URL` secret and Space build logs.