# Free Deployment Checklist

This project is set up for:

- Frontend: GitHub Pages
- Backend: Hugging Face Spaces (Docker)

## 1) Deploy backend (Hugging Face Space)

1. Open your Space: `diptidhawade/NoteScanner`.
2. Ensure the Space uses the root `Dockerfile` (already configured to run on `${PORT}` with default `7860`).
3. In Space **Settings -> Variables and secrets**, add the settings below.
4. Deploy the code, either automatically (recommended) or by hand:

### Automatic backend deploy (GitHub Actions)

The workflow `.github/workflows/deploy-backend.yml` pushes the backend to the Space on every push to `main`
(or when you run it by hand from the **Actions** tab).

1. On Hugging Face, create a **write** access token: **Settings -> Access Tokens -> New token** (type: Write).
2. In your GitHub repo, go to **Settings -> Secrets and variables -> Actions -> New repository secret** and add:
   - `HF_TOKEN` = that token
3. Push to `main`. Without `HF_TOKEN` the workflow skips the deploy and shows a notice instead of failing.

What it sends to the Space: `backend/`, `Dockerfile`, `.dockerignore`, `requirements.txt`, `DEPLOYMENT.md` and
`README.md`. The frontend isn't needed there (it lives on GitHub Pages), and no binary files are sent
(Spaces reject them unless they use Git LFS).

### Space README front matter

Hugging Face reads the Space's settings from YAML at the very top of its `README.md`:

```yaml
---
title: NoteScanner
emoji: "📚"
colorFrom: indigo
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
---
```

The GitHub `README.md` doesn't carry this block. The deploy workflow adds it to the Space's copy
automatically. If you upload files to the Space by hand, keep this block at the top of the Space's `README.md`.

### Manual deploy (only if you don't use the workflow)

In the Space's **Files** tab, upload the same files the workflow sends (`backend/`, `Dockerfile`,
`.dockerignore`, `requirements.txt`, and `README.md` with the front matter above).

### Required Space secrets

- `SARVAM_API_KEY`

### Recommended Space variables/secrets

- `SARVAM_API_BASE` (optional, default works)
- `SARVAM_MODEL_RAG` (optional)
- `SARVAM_MODEL_STUDY` (optional)
- `SARVAM_DOC_INTEL_LANGUAGE` (optional)
- `SARVAM_DOC_INTEL_TIMEOUT` (optional)

### Security settings

- `CORS_ORIGINS` (only needed if your frontend is not at `https://cherie-dips.github.io`):
  the website(s) allowed to call the backend, comma-separated, e.g. `https://<your-username>.github.io`
- Optional: `SESSION_TTL_DAYS`, `MAX_UPLOAD_MB`, `MAX_CHAT_UPLOAD_CHARS`, `CHAT_CACHE_TTL_SECONDS`
- Request limits (overall per address/browser, logins, uploads, questions, study tools, daily AI caps)
  are the `LIMIT_*` values in `backend/settings.py`; see "Request limits" in the README. To loosen or
  tighten all of them without a code change, set `RATE_LIMIT_MULTIPLIER` (e.g. `2` doubles every limit).

### Password-reset emails (optional)

"Forgot password" emails are only turned on when both `SMTP_HOST` and `SMTP_FROM` are set.
Without them, the app explains that password reset by email isn't available.

- `FRONTEND_URL`: the address people open the app at, used to build the reset link,
  e.g. `https://cherie-dips.github.io/NoteScanner/`
- `SMTP_HOST`: your email provider's SMTP server, e.g. `smtp.gmail.com`
- `SMTP_PORT`: usually `587`
- `SMTP_USERNAME` and `SMTP_PASSWORD`: the login for that server (for Gmail, an app password)
- `SMTP_FROM`: the sender address shown to users, e.g. `NoteScanner <noreply@example.com>`
- `SMTP_STARTTLS`: `true` (default) for port 587

The same email settings send **daily review reminders** to students who turn them on in Account
settings: `REMINDER_HOUR` (default `8`, in `APP_TIMEZONE`, default `Asia/Kolkata`) is the hour after
which the day's emails go out; `REMINDERS_ENABLED=false` turns the feature off.

### Running a pilot (optional settings)

- `ADMIN_EMAILS`: comma-separated emails that can open "Usage & feedback" in the profile menu
  (active students, questions, uploads, pages read, reviews, ratings, feedback, estimated cost).
- `ALERT_WEBHOOK_URL`: a Slack or Discord incoming-webhook address. Server crashes, failed uploads,
  AI outages and failed reminder emails are posted there (each kind at most once per 10 minutes).
- AI cost estimate: `AI_PRICE_PER_PAGE` (Sarvam Vision, per page), `AI_PRICE_PER_1K_INPUT_TOKENS`,
  `AI_PRICE_PER_1K_OUTPUT_TOKENS` and `AI_PRICE_CURRENCY` (default `INR`). Copy the prices from
  your Sarvam plan; until they're set the admin page shows usage counts but no cost.
- `AI_MONTHLY_BUDGET`: once this month's estimated cost reaches it, reading files, answers and study
  tools pause until next month (notes and flashcard reviews keep working). `0` (default) = no limit.
  Needs the prices above.
- `STARTER_NOTES=false`: don't give new accounts the "Getting started" folder.

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
3. Check it's up: open `https://diptidhawade-NoteScanner.hf.space/health` (should show `{"status":"ok"}`).

## 2) Configure GitHub Pages frontend

1. In your GitHub repo, go to **Settings -> Secrets and variables -> Actions -> New repository secret**.
2. Add this secret:

- `VITE_API_URL` = `https://diptidhawade-NoteScanner.hf.space`

1. Push to `main` (or rerun workflow `Deploy to GitHub Pages`).
2. GitHub Pages URL will be:
  - `https://<your-username>.github.io/NoteScanner/`

The Pages workflow installs exact versions from `frontend/my-app/package-lock.json` (`npm ci`) and runs the
code checker (ESLint) before building, so commit the lockfile whenever you add or update packages.

## 3) Checks on every push (CI)

`.github/workflows/ci.yml` runs on every push to `main` and on pull requests:

- Backend: installs the pinned packages and runs the tests in `tests/` (no AI service or database needed).
- Frontend: runs ESLint and a production build.

## 4) Verify

1. Open frontend URL.
2. Confirm these work:
  - `+` chat upload
  - query response
  - explorer upload
  - study generation

## 5) Shared course library and SDE-Prep (Study AI + Ask AI)

[SDE-Prep](https://cherie-dips.github.io/SDE-Prep/) uses this backend in two ways:

- **Study AI tab**: this frontend inside SDE-Prep (`/NoteScanner/?embed=sde`), in SDE-Prep's colours.
  Both sites are on `cherie-dips.github.io`, so they share the sign-in.
- **Ask AI** in SDE-Prep's Notes tab: questions, flashcards, quizzes and summaries about the course PDFs
  shown there. Those PDFs are read **once** on the server into the shared course library
  (`backend/library.py`) and searched by every student; nothing is copied per student.

`https://cherie-dips.github.io` is already allowed by the default `CORS_ORIGINS`.

### Index the course PDFs (once)

From your computer, with `.env` pointing at Chroma Cloud and holding `SARVAM_API_KEY`:

```bash
python scripts/index_library.py --dry-run   # lists the PDFs and how many pieces need OCR, and the cost
python scripts/index_library.py             # reads and indexes them
```

Handwritten and scanned pages are read with Sarvam Vision (≈ ₹0.5 per piece at Sarvam's listed price;
`--dry-run` prints the total first). Typed pages are read for free. Without a Sarvam key the script uses
Tesseract instead (free, much weaker on handwriting). Running it again only reads new or changed PDFs.

Or from the server, as an admin (`ADMIN_EMAILS`): `POST /admin/library/sync` starts a sync in the
background, and `GET /admin/library` shows each PDF's status.

### Keep it up to date

- After adding PDFs to the Supabase bucket, run the script again (or the admin sync), or
- set `LIBRARY_SYNC_HOURS` (e.g. `24`) on the Space to check the bucket automatically.

Library OCR counts towards `AI_MONTHLY_BUDGET`; when the budget is used up, PDFs that need OCR wait for
the next sync (typed PDFs are still indexed).

### Settings (all optional)

| Setting | Default | What it does |
|---|---|---|
| `LIBRARY_ENABLED` | `true` | Turn the shared library (and Ask AI's course search) on or off |
| `LIBRARY_SUPABASE_URL`, `LIBRARY_SUPABASE_KEY`, `LIBRARY_BUCKET` | SDE-Prep's bucket | Where the PDFs are (the key is the public, read-only anon key) |
| `LIBRARY_PREFIXES` | `plaksha-university` | Bucket folders to index (comma-separated) |
| `LIBRARY_OCR` | `auto` | `auto` (Sarvam if a key is set, else Tesseract), `sarvam`, `tesseract` or `off` |
| `LIBRARY_SYNC_HOURS` | `0` | Re-check the bucket every N hours (`0` = only when started by hand) |

### Verify

1. `GET /library/status` lists the indexed PDFs.
2. In SDE-Prep, open Notes → a course PDF → **Ask AI**: sign in, ask a question, click a source
   (the PDF scrolls to that spot).
3. Open the **Study AI** tab: you are already signed in, and a deck saved from Ask AI is under Review.

## Notes

- Backend on free HF Space may sleep when idle (cold start delay on first request). The search model is
  downloaded while the image is built, so waking up doesn't download it again.
- The backend must run as a single process (one uvicorn worker, as the `Dockerfile` does): the chat-upload
  cache, request limits and per-user locks live in that process's memory.
- If frontend shows backend/network errors, first check `VITE_API_URL` secret and Space build logs.
- If the browser console shows a CORS error, add your frontend's address to `CORS_ORIGINS`.
- After this update everyone has to sign in once more (older logins had no expiry date and are now treated as expired).

## Local development with Docker Compose

`docker compose up --build` starts a local Chroma server, the backend on `http://localhost:8000` and the
frontend dev server on `http://localhost:5173`. Local Chroma data is kept in the `chroma_data` volume
(mounted at `/data`, where Chroma 1.x stores it).

## Backups and restore

Chroma Cloud holds the only copy of every student's account, notes, folders and flashcard decks.
Regular backups, and checking that they can be restored, are how this project protects against
losing that data (a bad deploy, an accidental delete, or a problem with the Chroma account).

### Weekly automatic backup

`.github/workflows/backup.yml` runs every Sunday at 03:00 India time (and on demand from the
Actions tab → "Weekly backup" → Run workflow). Add these repository secrets (Settings → Secrets and
variables → Actions → Secrets):

- `CHROMA_API_KEY`, `CHROMA_TENANT`, `CHROMA_DATABASE`: the same values as on the Space.
- `BACKUP_PASSPHRASE`: a long random passphrase. **Store it somewhere safe outside GitHub** (a
  password manager): without it the backups can't be opened.

Until all four are set, the workflow skips with a notice. Each run saves one encrypted file as a
workflow artifact (kept 90 days). The backup holds password hashes and students' notes, so it's
encrypted before upload: artifacts of a public repository can be downloaded by others.

### Back up by hand

```bash
python scripts/backup_chroma.py                      # writes notescanner-backup-<UTC time>.jsonl.gz
python scripts/backup_chroma.py --out my-backup.jsonl.gz
```

It reads the Chroma settings from `.env` (or the environment). Login sessions, password-reset links
and OneNote sign-in tokens are left out on purpose: after a restore, students sign in again and
reconnect OneNote. Keep backup files private.

### Open an encrypted backup

Download the artifact from the workflow run (it's a zip containing the `.gpg` file), then:

```bash
gpg --decrypt notescanner-backup-<time>.jsonl.gz.gpg > notescanner-backup.jsonl.gz
```

(gpg asks for `BACKUP_PASSPHRASE`.)

### Restore

```bash
python scripts/restore_chroma.py notescanner-backup.jsonl.gz --dry-run   # check the file, change nothing
python scripts/restore_chroma.py notescanner-backup.jsonl.gz
```

It restores into the database configured in `.env`. It refuses to write into collections that
already have records, so it can't mix old data into live data by accident; `--force` writes anyway
(records with the same id are replaced, others are kept). It also refuses a backup file that was cut
off before it finished.

### Test a restore (do this at least once, and after big changes)

A backup you have never restored isn't proof you can recover. To test without touching live data:

1. Create a second, empty database in Chroma Cloud (e.g. `notescanner-restore-test`).
2. Restore into it by pointing the settings at that database for one command:
   `CHROMA_DATABASE=notescanner-restore-test python scripts/restore_chroma.py notescanner-backup.jsonl.gz`
   (environment variables you set like this take priority over `.env`).
3. Run the backend locally against that database the same way
   (`CHROMA_DATABASE=notescanner-restore-test uvicorn backend.api:app --port 8000`) and check you can
   sign in, see your folders and files, ask a question and review a deck.
4. Delete the test database afterwards.

## Uptime check

`.github/workflows/uptime.yml` calls `<backend>/health` every 15 minutes and fails if it doesn't
answer `{"status":"ok"}` within 30 seconds. GitHub emails a failed run to the person who last changed
the workflow's schedule. To turn it on, add the repository variable `BACKEND_URL` (Settings → Secrets
and variables → Actions → Variables), e.g. `https://diptidhawade-NoteScanner.hf.space`.

- A free Space that has gone to sleep can take longer than 30 seconds to wake up, so expect a failure
  now and then until the backend is on always-on hosting. The regular checks also keep a free Space awake.
- GitHub pauses scheduled workflows in repositories with no activity for 60 days; re-enable it from the
  Actions tab if that happens.

## Answer-quality test

`scripts/answer_quality.py` signs in to a running backend, asks a fixed list of questions and scores
whether each answer contains the key points it should (0–100 overall). Run it before and after
changing the AI prompts, the AI model or the search settings, and compare the two runs:

```bash
export NOTESCANNER_PASSWORD='test account password'
python scripts/answer_quality.py --api https://diptidhawade-NoteScanner.hf.space \
  --email answer-test@example.com --questions evals/sample_questions.json \
  --baseline evals/results/<earlier run>.json --min-score 70 --max-drop 5
```

Use a separate test account. `evals/sample_questions.json` works with the "Getting started" notes every
new account receives; write about 30 questions from a real course's notes as described in
`evals/README.md`. Results are saved in `evals/results/`.
