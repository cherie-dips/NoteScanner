# Plan: from working project to something students rely on

NoteScanner has the core of a study tool: upload notes, ask questions answered from them, flashcards
with spaced repetition, practice MCQs, an exam planner, and accounts. The software for Steps 0–2
below is **built**. What's left is mostly work only the project owner can do (accounts, money,
approvals, and real students) — listed as **Your part** in each step.

Legend: ✅ built and tested · 👤 your part

---

## Step 0: Before anyone else uses it (about 1 week)

| Task | Status |
|---|---|
| **Back up, then deploy** | ✅ `scripts/backup_chroma.py` / `restore_chroma.py`, and a weekly encrypted backup workflow (DEPLOYMENT.md → "Backups and restore"). 👤 Take a backup by hand, deploy, then do the checks in DEPLOYMENT.md → "Verify". The first run moves old uploads to the new storage format. |
| **Always-on hosting** | ✅ `/health` route and an uptime workflow that checks it every 15 minutes. 👤 Move the backend to hardware that doesn't sleep (paid Space hardware or a small cloud server) and set the `BACKEND_URL` repo variable. |
| **Monthly AI budget** | ✅ Usage is counted per student per day (questions, uploads, pages read by Sarvam Vision, AI tokens). With prices set, the admin page shows the estimated cost, and `AI_MONTHLY_BUDGET` pauses paid AI work when reached. 👤 Copy prices from your Sarvam plan into `AI_PRICE_*`, use it yourself for a week to see the cost per student, then set the budget. |
| **Password-reset email** | ✅ Built (forgot-password form, one-time links that expire in 1 hour). 👤 Set `SMTP_*` and `FRONTEND_URL` on the Space and try it. |
| **Privacy note** | ✅ A plain-language privacy note, linked from the landing page, sign-up and the profile menu; sign-up requires accepting it, and the acceptance date and version are stored. 👤 The text is a **draft**: fill in the contact line, check it matches what you actually use, and get the college's approval if it requires one. |
| **Error alerts** | ✅ Crashes, failed uploads, AI outages and failed reminder emails go to a Slack/Discord webhook (each kind at most once per 10 minutes). 👤 Create a webhook and set `ALERT_WEBHOOK_URL`. |
| **Regular backups** | ✅ Weekly, encrypted, kept 90 days as a GitHub artifact. 👤 Add the secrets (`CHROMA_*`, `BACKUP_PASSPHRASE`) and **practise a restore** into a separate database once. |

## Step 1: Small pilot with one course (2–4 weeks)

| Task | Status |
|---|---|
| Starter notes for new accounts | ✅ Every new account gets a "Getting started" folder (how to use NoteScanner + sample physics notes) so a first question works immediately, plus a three-step tips card. |
| Answer ratings and a feedback form | ✅ 👍/👎 under every answer (👎 asks "what was wrong?"), and "Send feedback" in the profile menu. |
| Answer-quality test | ✅ `scripts/answer_quality.py` with 10 sample questions (`evals/`). 👤 Write ~30 questions from the pilot course with the key phrases a right answer must contain (see `evals/README.md`), run it once to get a baseline, and re-run after any change to prompts, model or search settings. |
| Weekly numbers | ✅ "Usage & feedback" in the profile menu for emails in `ADMIN_EMAILS`: active students, questions, uploads, pages read, failed uploads, reviews, helpful share, estimated cost, per-day table, recent feedback. 👤 Look at it every week and note the numbers. |
| 👤 **Run the pilot** | Pick one course and 15–30 volunteers a few weeks before a mid-term. Share the link, the privacy note, and a 2-minute demo. |
| 👤 **Talk to five students** at the end | What did they use before the exam, what did they ignore, what was confusing? |

Move on when at least half the pilot students come back in the week before the exam, most answers
are rated helpful, and nobody lost notes.

## Step 2: Make studying a habit (built; tune it with pilot feedback)

| Feature | Status |
|---|---|
| Daily review reminder | ✅ Opt-in email ("12 flashcards are due today"), sent once a day after `REMINDER_HOUR`. Needs SMTP. |
| Study dashboard | ✅ "Today" tab: cards due, reviewed today, streak, weak topics (files with low quiz scores), 14-day activity, today's exam-plan tasks. |
| Exam mode | ✅ "Exam" tab: pick a course folder and an exam date for a day-by-day plan (revision days, a second pass if there's time, a mock test the day before); timed mock tests mixing questions from the whole folder, with review and score. |
| Flashcards on upload | ✅ "Make flashcards" on each finished upload, or turn on "Make flashcards automatically". |
| Ask about a highlighted passage | ✅ Select text in a file → "Ask about this". |
| Page numbers in sources | ✅ Sources show "page N" for PDFs. Exact when PyMuPDF reads the PDF; for Sarvam-read PDFs the pages are matched using Sarvam's per-page output (best effort — check it on a few real scanned PDFs). |
| Answers in the student's language | ✅ English or one of 10 Indian languages, for answers and study material (Account settings). |
| Install on phone | ✅ Installable app (PWA): add to home screen from the browser menu. |

👤 Your part in Step 2: watch which of these pilot students actually use (the admin page shows reviews
and questions per day), and drop or improve what they don't.

## Step 3: Grow beyond a pilot (not built yet)

Do these once more than one course is using it:

- **Shared course spaces**: an instructor or TA uploads the official notes once and every enrolled
  student can search them. Better answers (official material), lower cost (read once, not once per
  student), and instant onboarding.
- **Sign in with Google** (college accounts) instead of separate passwords.
- **A regular database for accounts, folders and decks** (for example Postgres), keeping Chroma for
  search. It needs hosting with a lasting database, which the free Hugging Face Space doesn't have.
- **Run more than one server copy**: upload jobs, the chat cache, request limits and usage caches live
  in one process's memory today. Move them to Redis (the `.env` already has `REDIS_HOST`, unused).
- **Load test** with ~100 students at once, and an **accessibility check** (keyboard use, screen readers, contrast).

---

## What "usable" means: targets to check

- A new student can upload notes and get a useful answer in **under 5 minutes**, without help.
- Answers start appearing in **under 5 seconds**; a typical PDF is ready in **under 2 minutes**.
- **No lost notes**, ever: backups exist and have been restored at least once as a test.
- At least **70%** of rated answers are 👍 and the answer-quality score doesn't drop between releases.
- AI cost per active student per month is **known and within budget**.
- Students who used it in the pilot **say they'd use it for the next exam**.

## Biggest risks

| Risk | What to do |
|---|---|
| Answers that sound right but are wrong | Ratings, the answer-quality test, and the "Beyond notes" label for anything not from the notes |
| AI costs grow faster than expected | Per-user daily caps, the monthly budget, shared course spaces later |
| Free hosting sleeps or restarts | Always-on hosting before the pilot; the uptime check tells you when it's down |
| Student data concerns | Privacy note, delete-account (removes notes, usage counts and feedback too), college approval, encrypted backups |
| One person maintaining everything | The test suite and CI catch breakage; error alerts tell you about problems; keep this plan and the README current |
