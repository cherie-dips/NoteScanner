"""The answer-quality test (scripts/answer_quality.py): scoring rules and a full run against the app."""
import json
from pathlib import Path

import pytest

from backend import llm_pipeline
from backend.starter_notes import add_starter_notes
from scripts import answer_quality as aq

SAMPLE_QUESTIONS = Path(__file__).resolve().parents[1] / "evals" / "sample_questions.json"


def test_phrases_ignore_case_spacing_and_math_markup():
    assert aq.phrase_found("F = ma", "The law is $F=ma$.")
    assert aq.phrase_found("dp", r"$F = \frac{dp}{dt}$")
    assert aq.phrase_found("Text view", "switch to **text view** and fix it")
    assert aq.phrase_found("newton", "measured in newtons (N)")  # plural ending allowed
    assert not aq.phrase_found("inertia", "mass times acceleration")
    assert not aq.phrase_found("rest", "compound interest")  # whole words only
    assert not aq.phrase_found("6 N", "So the net force is 6 newtons.")
    assert aq.phrase_found(["6 N", "6 newton"], "So the net force is 6 newtons.")  # alternatives


def test_score_answer_and_sources():
    assert aq.score_answer("rest and force", ["rest", "straight line", "force"]) == (2 / 3, ["straight line"])
    assert aq.score_answer("anything", []) == (1.0, [])
    assert aq.score_answer("6 N", [["6 N", "6 newton"], "kg"]) == (0.5, ["kg"])
    assert aq.score_answer("", [["6 N", "6 newton"]]) == (0.0, ["6 N / 6 newton"])
    sources = [{"metadata": {"path": "Getting started/Sample - Newton's laws of motion.md"}}]
    assert aq.source_found(sources, "newton's laws") is True
    assert aq.source_found(sources, "Welcome") is False
    assert aq.source_found(sources, None) is None


def test_question_score_averages_the_parts_that_apply():
    q = {"id": "a", "question": "?", "must_include": ["x", "y"], "expected_source": "notes.md"}
    r = aq.score_question(q, "x only", [{"metadata": {"path": "C/notes.md"}}])
    assert r["score"] == 0.75 and r["missing"] == ["y"] and r["source_ok"] is True
    r = aq.score_question({"id": "b", "question": "?", "must_include": ["x"]}, "x", [])
    assert r["score"] == 1.0 and r["source_ok"] is None
    r = aq.score_question(q, "", [], error="HTTP 503: AI down")
    assert r["score"] == 0.0 and r["error"]


def test_overall_baseline_comparison_and_gate():
    before = {"overall_score": 80.0, "results": [{"id": "a", "score": 1.0}, {"id": "b", "score": 0.6}]}
    after = {"overall_score": 65.0, "results": [{"id": "a", "score": 0.5}, {"id": "b", "score": 0.8}, {"id": "new", "score": 1}]}
    assert aq.overall_score(after["results"][:2]) == 65.0
    cmp = aq.compare_with_baseline(after, before)
    assert cmp["overall_change"] == -15.0
    assert cmp["worse"] == [{"id": "a", "before": 1.0, "after": 0.5}]
    assert [b["id"] for b in cmp["better"]] == ["b"]
    assert aq.gate(after, cmp, min_score=70, max_drop=5) == [
        "overall score 65.0 is below the minimum 70",
        "overall score dropped 15.0 points (allowed: 5)",
    ]
    assert aq.gate(after, cmp, min_score=None, max_drop=None) == []


def test_questions_file_is_checked(tmp_path):
    bad = tmp_path / "q.json"
    bad.write_text(json.dumps([{"id": "a", "question": "?"}, {"id": "a", "question": "??"}]))
    with pytest.raises(aq.SetupError, match="unique"):
        aq.load_questions(bad)
    bad.write_text("{}")
    with pytest.raises(aq.SetupError):
        aq.load_questions(bad)
    assert len(aq.load_questions(SAMPLE_QUESTIONS)) == 10


class FakeResponse:
    def __init__(self, status, body, headers=None):
        self.status_code, self._body, self.headers, self.text = status, body, headers or {}, json.dumps(body)

    def json(self):
        return self._body


def test_waits_and_retries_when_rate_limited():
    replies = [FakeResponse(429, {"detail": "Too many"}, {"retry-after": "7"}), FakeResponse(200, {"answer": "ok", "source_documents": []})]

    class Session:
        def post(self, *_a, **_k):
            return replies.pop(0)

    waits = []
    answer, sources, error = aq.ask(Session(), {}, {"question": "?"}, sleep=waits.append)
    assert (answer, error, waits) == ("ok", None, [7])


def test_full_run_against_the_app(client, user, monkeypatch, tmp_path, capsys):
    add_starter_notes(user["user_id"])  # what every new account starts with
    monkeypatch.setattr(llm_pipeline, "sarvam_rag_answer", lambda q, ctx: (f"From your notes:\n{ctx}", None))
    monkeypatch.setenv("NOTESCANNER_PASSWORD", "goodpass123")
    args = [
        "--api", "http://testserver", "--email", user["email"], "--questions", str(SAMPLE_QUESTIONS),
        "--results-dir", str(tmp_path), "--delay", "0", "--min-score", "60",
    ]
    assert aq.main(args, session=client, sleep=lambda s: None) == 0
    first = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert first["overall_score"] >= 60 and len(first["results"]) == 10
    assert "Overall score" in capsys.readouterr().out

    # Answers get worse: compared with the first run, the drop is reported and the run fails.
    monkeypatch.setattr(llm_pipeline, "sarvam_rag_answer", lambda q, ctx: ("I'm not sure.", None))
    worse_dir = tmp_path / "second"
    code = aq.main(
        args[:-2] + ["--results-dir", str(worse_dir), "--baseline", str(next(tmp_path.glob("*.json"))), "--max-drop", "5"],
        session=client,
        sleep=lambda s: None,
    )
    assert code == 1
    err = capsys.readouterr().err
    assert "dropped" in err
    second = json.loads(next(worse_dir.glob("*.json")).read_text())
    assert second["comparison"]["worse"]


def test_missing_password_is_a_setup_error(monkeypatch, capsys):
    monkeypatch.delenv("NOTESCANNER_PASSWORD", raising=False)
    assert aq.main(["--email", "a@b.co"]) == 2
    assert "NOTESCANNER_PASSWORD" in capsys.readouterr().err
