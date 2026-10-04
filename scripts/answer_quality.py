"""
Answer-quality test: ask a fixed set of questions and check the answers contain the key points.

    export NOTESCANNER_PASSWORD='...'
    python scripts/answer_quality.py --api https://diptidhawade-NoteScanner.hf.space \\
        --email you@example.com --questions evals/sample_questions.json \\
        --baseline evals/results/<previous run>.json --min-score 70 --max-drop 5

Each question is scored 0–1:
- key phrases: the share of `must_include` phrases found in the answer (case and spacing ignored);
- source (only if `expected_source` is set): 1 if a cited source's path contains that text, else 0.
The question's score is the average of the parts that apply. The overall score is the average × 100.

Results are saved to evals/results/<UTC time>.json. Exit code: 0 = passed, 1 = score below
--min-score or dropped more than --max-drop compared with --baseline, 2 = couldn't run.
See evals/README.md for how to write questions.
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS_DIR = ROOT / "evals" / "results"
MAX_RETRIES_WHEN_LIMITED = 2


class SetupError(Exception):
    """The test couldn't run (bad questions file, sign-in failed, ...)."""


# ---------- Scoring (pure functions) ----------
def normalize(text: str) -> str:
    """Lower-case, simplify Markdown/LaTeX, collapse spaces: '$F = \\frac{dp}{dt}$' → 'f = dp/dt'."""
    text = (text or "").lower()
    text = re.sub(r"\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}", r"\1/\2", text)
    text = re.sub(r"[$*`_\\{}]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def phrase_found(phrase, answer: str) -> bool:
    """
    Whole-word match ('rest' doesn't match 'interest'), allowing a plural ending ('newton' matches
    'newtons'). A list means alternatives: ["6 N", "6 newton"]. Formulas also match without spaces.
    """
    if isinstance(phrase, (list, tuple)):
        return any(phrase_found(p, answer) for p in phrase)
    p, a = normalize(str(phrase)), normalize(answer)
    if not p:
        return True
    if re.search(r"(?<![a-z0-9])" + re.escape(p) + r"(?:e?s)?(?![a-z0-9])", a):
        return True
    compact = p.replace(" ", "")
    return " " in p and not compact.isalnum() and compact in a.replace(" ", "")


def _label(phrase) -> str:
    return " / ".join(map(str, phrase)) if isinstance(phrase, (list, tuple)) else str(phrase)


def score_answer(answer: str, must_include: list) -> tuple[float, list[str]]:
    """(share of key phrases found, the phrases that were missing)."""
    phrases = [p for p in (must_include or []) if (p if isinstance(p, (list, tuple)) else str(p).strip())]
    if not phrases:
        return 1.0, []
    missing = [_label(p) for p in phrases if not phrase_found(p, answer)]
    return (len(phrases) - len(missing)) / len(phrases), missing


def source_found(sources: list[dict], expected: str | None) -> bool | None:
    """None when no source is expected; otherwise whether a cited path contains the expected text."""
    if not expected:
        return None
    want = expected.lower()
    for s in sources or []:
        path = str(((s or {}).get("metadata") or {}).get("path") or "")
        if want in path.lower():
            return True
    return False


def score_question(question: dict, answer: str, sources: list[dict], error: str | None = None) -> dict:
    keyword_score, missing = score_answer(answer, question.get("must_include") or [])
    src = source_found(sources, question.get("expected_source"))
    if error:
        missing = [_label(p) for p in question.get("must_include") or []]
        keyword_score, src = 0.0, (False if src is not None else None)
    parts = [keyword_score] + ([1.0 if src else 0.0] if src is not None else [])
    return {
        "id": question.get("id") or question.get("question"),
        "question": question.get("question"),
        "score": round(sum(parts) / len(parts), 4),
        "key_phrase_score": round(keyword_score, 4),
        "missing": missing,
        "source_ok": src,
        "error": error,
        "answer": (answer or "")[:2000],
        "sources": sorted({str(((s or {}).get("metadata") or {}).get("path") or "") for s in sources or []}),
    }


def overall_score(results: list[dict]) -> float:
    if not results:
        return 0.0
    return round(100 * sum(r["score"] for r in results) / len(results), 1)


def compare_with_baseline(current: dict, baseline: dict) -> dict:
    """{"overall_change": points, "worse": [{"id", "before", "after"}], "better": [...]} by question id."""
    before = {r["id"]: r["score"] for r in baseline.get("results") or []}
    worse, better = [], []
    for r in current.get("results") or []:
        if r["id"] not in before:
            continue
        delta = r["score"] - before[r["id"]]
        row = {"id": r["id"], "before": before[r["id"]], "after": r["score"]}
        if delta < -1e-9:
            worse.append(row)
        elif delta > 1e-9:
            better.append(row)
    return {
        "overall_change": round(current.get("overall_score", 0.0) - baseline.get("overall_score", 0.0), 1),
        "worse": worse,
        "better": better,
    }


def gate(current: dict, comparison: dict | None, min_score: float | None, max_drop: float | None) -> list[str]:
    """Reasons the run fails (empty list = passed)."""
    problems = []
    if min_score is not None and current["overall_score"] < min_score:
        problems.append(f"overall score {current['overall_score']} is below the minimum {min_score}")
    if comparison and max_drop is not None and -comparison["overall_change"] > max_drop:
        problems.append(f"overall score dropped {-comparison['overall_change']} points (allowed: {max_drop})")
    return problems


# ---------- Running against a NoteScanner server ----------
def load_questions(path: str | Path) -> list[dict]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise SetupError(f"Can't read questions file {path}: {e}") from e
    if not isinstance(data, list) or not data:
        raise SetupError(f"{path} must be a JSON list of questions.")
    for i, q in enumerate(data):
        if not isinstance(q, dict) or not str(q.get("question") or "").strip():
            raise SetupError(f"Question #{i + 1} in {path} has no 'question' text.")
        q.setdefault("id", f"q{i + 1}")
    ids = [q["id"] for q in data]
    if len(set(ids)) != len(ids):
        raise SetupError(f"Question ids in {path} must be unique.")
    return data


def sign_in(session, email: str, password: str) -> dict:
    r = session.post("/login", data={"email": email, "password": password})
    if r.status_code != 200:
        try:
            msg = r.json().get("error") or r.json().get("detail")
        except Exception:
            msg = r.text[:200]
        raise SetupError(f"Sign-in failed (HTTP {r.status_code}): {msg}")
    return {"X-Session-Id": r.json()["session_id"]}


def ask(session, headers: dict, question: dict, sleep=time.sleep) -> tuple[str, list[dict], str | None]:
    """(answer, sources, error). Waits and retries when the server's request limit is reached."""
    data = {"query": question["question"], "include_course_context": "true"}
    if question.get("opened_file"):
        data["opened_file_path"] = question["opened_file"]
    for attempt in range(MAX_RETRIES_WHEN_LIMITED + 1):
        r = session.post("/query_folder", data=data, headers=headers)
        if r.status_code == 429 and attempt < MAX_RETRIES_WHEN_LIMITED:
            wait = min(int(r.headers.get("retry-after") or 60), 600)
            print(f"  request limit reached; waiting {wait}s", flush=True)
            sleep(wait)
            continue
        break
    try:
        body = r.json()
    except Exception:
        body = {}
    if r.status_code != 200:
        return "", [], f"HTTP {r.status_code}: {body.get('error') or body.get('detail') or r.text[:200]}"
    return body.get("answer") or "", body.get("source_documents") or [], None


def run(session, email: str, password: str, questions: list[dict], delay: float = 1.0, sleep=time.sleep) -> list[dict]:
    headers = sign_in(session, email, password)
    results = []
    for i, q in enumerate(questions):
        if i and delay:
            sleep(delay)  # stay well under the server's 60-questions-per-10-minutes limit
        answer, sources, error = ask(session, headers, q, sleep=sleep)
        results.append(score_question(q, answer, sources, error))
    return results


def print_report(report: dict, comparison: dict | None) -> None:
    print(f"\n{'id':<22} {'score':>6}  missing / notes")
    for r in report["results"]:
        notes = []
        if r["error"]:
            notes.append(r["error"])
        if r["missing"] and not r["error"]:
            notes.append("missing: " + ", ".join(r["missing"]))
        if r["source_ok"] is False:
            notes.append("expected source not cited")
        print(f"{str(r['id'])[:22]:<22} {round(r['score'] * 100):>5}%  {'; '.join(notes)}")
    print(f"\nOverall score: {report['overall_score']} / 100 ({len(report['results'])} questions)")
    if comparison:
        sign = "+" if comparison["overall_change"] >= 0 else ""
        print(f"Change since baseline: {sign}{comparison['overall_change']} points")
        for w in comparison["worse"]:
            print(f"  worse: {w['id']}  {round(w['before'] * 100)}% → {round(w['after'] * 100)}%")


def main(argv: list[str] | None = None, session=None, sleep=time.sleep) -> int:
    parser = argparse.ArgumentParser(description="Ask a fixed set of questions and score NoteScanner's answers.")
    parser.add_argument("--api", default=os.getenv("NOTESCANNER_API", "http://localhost:8000"), help="backend URL")
    parser.add_argument("--email", default=os.getenv("NOTESCANNER_EMAIL"), help="account to sign in with")
    parser.add_argument("--questions", default=str(ROOT / "evals" / "sample_questions.json"))
    parser.add_argument("--baseline", help="results file from an earlier run to compare with")
    parser.add_argument("--min-score", type=float, help="fail if the overall score (0-100) is below this")
    parser.add_argument("--max-drop", type=float, help="fail if the score dropped more than this many points")
    parser.add_argument("--results-dir", default=str(DEFAULT_RESULTS_DIR))
    parser.add_argument("--delay", type=float, default=1.0, help="seconds between questions")
    args = parser.parse_args(argv)

    password = os.getenv("NOTESCANNER_PASSWORD")
    try:
        if not args.email or not password:
            raise SetupError("Give --email and set the NOTESCANNER_PASSWORD environment variable.")
        questions = load_questions(args.questions)
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8")) if args.baseline else None
        own_session = session is None
        if own_session:
            import httpx

            session = httpx.Client(base_url=args.api.rstrip("/"), timeout=180.0)
        try:
            results = run(session, args.email, password, questions, delay=args.delay, sleep=sleep)
        finally:
            if own_session:
                session.close()
    except SetupError as e:
        print(str(e), file=sys.stderr)
        return 2
    except Exception as e:  # network problems, bad baseline file, ...
        print(f"Couldn't run the answer-quality test: {e}", file=sys.stderr)
        return 2

    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "api": args.api,
        "questions_file": str(args.questions),
        "overall_score": overall_score(results),
        "results": results,
    }
    comparison = compare_with_baseline(report, baseline) if baseline else None
    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    out = results_dir / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    if out.exists():
        out = results_dir / f"{out.stem}-{int(time.time() * 1000) % 1000}.json"
    out.write_text(json.dumps({**report, "comparison": comparison}, indent=2, ensure_ascii=False), encoding="utf-8")

    print_report(report, comparison)
    print(f"Saved results to {out}")
    problems = gate(report, comparison, args.min_score, args.max_drop)
    for p in problems:
        print(f"FAILED: {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
