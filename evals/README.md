# Answer-quality test

`scripts/answer_quality.py` asks NoteScanner a fixed list of questions and checks that each answer
contains the key points it should. Run it before and after any change that could affect answers, so
you notice when answers get worse.

## Run it

```bash
export NOTESCANNER_PASSWORD='the test account password'
python scripts/answer_quality.py \
  --api https://diptidhawade-NoteScanner.hf.space \
  --email answer-test@example.com \
  --questions evals/sample_questions.json
```

- Use a **separate test account** that holds only the notes the questions are about (every new
  account starts with the "Getting started" notes that `sample_questions.json` is based on).
- The result is saved in `evals/results/<time>.json`. To compare with an earlier run, add
  `--baseline evals/results/<earlier file>.json`; the report lists the questions that got worse.
- Add `--min-score 70` and/or `--max-drop 5` to make the run fail (exit code 1) when the overall
  score is below 70, or drops by more than 5 points compared with the baseline.
- It waits about 1 second between questions so it stays within the server's request limits
  (60 questions per 10 minutes, 300 per day per account), and waits automatically if a limit is reached.

## How a question is scored

Each question gets 0–1:

- **Key phrases** (`must_include`): the share of the phrases found in the answer. Phrases match
  whole words (`rest` doesn't match "interest"), a plural ending is allowed (`newton` matches
  "newtons"), and capital letters and Markdown/LaTeX symbols are ignored, so `F = ma` also matches
  `$F=ma$`. When a point can be written two ways, give the options as a list:
  `["6 N", "6 newton"]` counts as found if either appears.
- **Source** (`expected_source`, optional): 1 if one of the cited notes has this text in its path.

The question's score is the average of the parts that apply; the overall score is the average of all
questions, from 0 to 100. A question that gets an error (for example the AI service was down) scores 0.

## Writing questions for a real course

Aim for **about 30 questions** per course, written from the course's real notes:

1. Upload the course notes to the test account, in one folder per course.
2. Write questions a student would really ask before an exam: definitions, "why" questions,
   formulas, a worked problem or two, and comparisons ("difference between X and Y").
3. For each question, list **2–4 key phrases** that any correct answer must contain, copied from
   the notes (a term, a formula, a number with its unit). Keep phrases short: `"F = ma"`, `"6 N"`,
   `"inertia"`, not whole sentences. Avoid phrases a correct answer could word differently.
4. Set `expected_source` to part of the file name the answer should come from.
5. Set `opened_file` (the note's full path) for questions a student would ask with that file open.

Format:

```json
[
  {
    "id": "unique-short-name",
    "question": "What does Newton's first law say?",
    "must_include": ["rest", "straight line", "force"],
    "expected_source": "Newton's laws of motion",
    "opened_file": "Physics/Lecture 2.pdf"
  }
]
```

## When to run it

- After changing the AI prompts (`backend/llm_pipeline.py`), the AI model (`SARVAM_MODEL_RAG`),
  the search settings (`MIN_SCORE_*`, `SOURCE_SCORE_WINDOW` in `backend/settings.py`) or how notes
  are split and searched (`backend/ingest_api.py`, `backend/search.py`).
- Before a release students will use, comparing with the last good run (`--baseline`).
- Read the answers of questions that got worse (they're saved in the results file): sometimes the
  answer is still right but worded differently, and the key phrases need adjusting.
