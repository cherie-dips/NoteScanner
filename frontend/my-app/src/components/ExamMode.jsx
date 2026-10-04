import { useCallback, useEffect, useRef, useState } from "react";
import Markdown from "./Markdown";
import { apiRequest, errorText } from "../auth";
import { recordMcqResult } from "../studyApi";

const COUNTS = [10, 15, 20];
const MINUTES = [10, 20, 30];

function todayIso() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function formatClock(seconds) {
  const s = Math.max(0, Math.ceil(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function formatPlanDate(iso) {
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
}

/**
 * "Exam" tab: (a) a day-by-day revision plan up to an exam date, (b) a timed mock test with questions
 * drawn from every file in a course folder.
 */
export default function ExamMode({ activeCourseFolder = "", presetCourse = "", presetKey = 0 }) {
  const [courses, setCourses] = useState([]);
  const [error, setError] = useState("");

  // Plan
  const [planCourse, setPlanCourse] = useState("");
  const [planDate, setPlanDate] = useState("");
  const [planBusy, setPlanBusy] = useState(false);
  const [exams, setExams] = useState([]);
  const [openExamId, setOpenExamId] = useState(null);

  // Mock test: phase setup | loading | running | done
  const [mockCourse, setMockCourse] = useState("");
  const [mockCount, setMockCount] = useState(10);
  const [mockMinutes, setMockMinutes] = useState(10);
  const [phase, setPhase] = useState("setup");
  const [items, setItems] = useState([]);
  const [answers, setAnswers] = useState([]);
  const [idx, setIdx] = useState(0);
  const [endsAt, setEndsAt] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  const [startedAt, setStartedAt] = useState(0);
  const finishedRef = useRef(false);
  const mockRef = useRef(null);

  const loadExams = useCallback(async () => {
    try {
      const data = await apiRequest("/study/exams");
      setExams(Array.isArray(data.exams) ? data.exams : []);
    } catch (err) {
      setError(errorText(err));
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    apiRequest("/list_tree")
      .then((data) => {
        if (cancelled) return;
        const folders = (data.tree || []).filter((n) => n.type === "folder").map((n) => n.path);
        setCourses(folders);
        const initial = folders.includes(activeCourseFolder) ? activeCourseFolder : folders[0] || "";
        setPlanCourse((c) => c || initial);
        setMockCourse((c) => c || initial);
      })
      .catch((err) => {
        if (!cancelled) setError(errorText(err));
      });
    void loadExams();
    return () => {
      cancelled = true;
    };
    // Load once when the tab opens; the course choice is the user's after that.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadExams]);

  // "Mock test" from the Today tab preselects that course.
  useEffect(() => {
    if (!presetKey || !presetCourse) return;
    setMockCourse(presetCourse);
    setPhase("setup");
    mockRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [presetKey, presetCourse]);

  const createPlan = async (e) => {
    e.preventDefault();
    if (!planCourse || !planDate) return;
    setPlanBusy(true);
    setError("");
    try {
      const data = await apiRequest("/study/exam_plan", {
        method: "POST",
        form: { course_path: planCourse, exam_date: planDate },
      });
      setOpenExamId(data.exam_id);
      await loadExams();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setPlanBusy(false);
    }
  };

  const deleteExam = async (exam) => {
    if (!window.confirm(`Delete the plan for the ${exam.course} exam?`)) return;
    try {
      await apiRequest(`/study/exams/${encodeURIComponent(exam.exam_id)}`, { method: "DELETE" });
      setExams((prev) => prev.filter((x) => x.exam_id !== exam.exam_id));
    } catch (err) {
      setError(errorText(err));
    }
  };

  const finishTest = useCallback(
    (finalAnswers) => {
      if (finishedRef.current) return;
      finishedRef.current = true;
      setPhase("done");
      const correct = items.reduce((n, q, i) => n + (finalAnswers[i] === q.answer_index ? 1 : 0), 0);
      void recordMcqResult(mockCourse, correct, items.length);
    },
    [items, mockCourse],
  );

  // Countdown while the test is running.
  useEffect(() => {
    if (phase !== "running") return;
    const timer = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(timer);
  }, [phase]);

  const remaining = phase === "running" ? (endsAt - now) / 1000 : 0;
  useEffect(() => {
    if (phase === "running" && remaining <= 0) finishTest(answers);
  }, [phase, remaining, answers, finishTest]);

  const startTest = async (e) => {
    e.preventDefault();
    if (!mockCourse) return;
    setPhase("loading");
    setError("");
    try {
      const data = await apiRequest("/study/exam", {
        method: "POST",
        form: { course_path: mockCourse, count: String(mockCount) },
      });
      const qs = (Array.isArray(data.items) ? data.items : []).filter(
        (x) => x && typeof x.question === "string" && Array.isArray(x.options) && x.options.length >= 2,
      );
      if (!qs.length) throw new Error("No questions could be made for this course.");
      finishedRef.current = false;
      setItems(qs);
      setAnswers(qs.map(() => null));
      setIdx(0);
      const start = Date.now();
      setStartedAt(start);
      setNow(start);
      setEndsAt(start + mockMinutes * 60 * 1000);
      setPhase("running");
    } catch (err) {
      setError(errorText(err));
      setPhase("setup");
    }
  };

  const pick = (optionIdx) => {
    const next = [...answers];
    next[idx] = optionIdx;
    setAnswers(next);
    if (next.every((a) => a != null)) {
      finishTest(next);
      return;
    }
    // Move on to the next unanswered question.
    for (let step = 1; step <= next.length; step++) {
      const j = (idx + step) % next.length;
      if (next[j] == null) {
        setIdx(j);
        break;
      }
    }
  };

  const score = items.reduce((n, q, i) => n + (answers[i] === q.answer_index ? 1 : 0), 0);
  const current = items[idx];
  const minutesUsed = Math.max(1, Math.round(((phase === "done" ? Math.min(now, endsAt) : now) - startedAt) / 60000));

  return (
    <div className="study-section exam-mode">
      {error && <div className="study-error">{error}</div>}
      {courses.length === 0 && (
        <p className="study-hint">Create a folder for a course and upload notes into it to use exam mode.</p>
      )}

      {phase !== "running" && (
        <>
          <h4 className="today-heading">Revision plan</h4>
          <form className="exam-plan-form" onSubmit={createPlan}>
            <label className="exam-field">
              <span>Course</span>
              <select
                className="exam-course-select"
                value={planCourse}
                onChange={(e) => setPlanCourse(e.target.value)}
                disabled={!courses.length}
              >
                {courses.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </label>
            <label className="exam-field">
              <span>Exam date</span>
              <input
                type="date"
                className="exam-date-input"
                min={todayIso()}
                value={planDate}
                onChange={(e) => setPlanDate(e.target.value)}
                required
              />
            </label>
            <button
              type="submit"
              className="study-action exam-plan-submit"
              disabled={planBusy || !planCourse || !planDate}
            >
              {planBusy ? "Planning…" : "Make a plan"}
            </button>
          </form>

          {exams.length > 0 && (
            <ul className="exam-list">
              {exams.map((x) => (
                <li key={x.exam_id} className="exam-item">
                  <div className="exam-item-head">
                    <button
                      type="button"
                      className="exam-item-toggle"
                      aria-expanded={openExamId === x.exam_id}
                      onClick={() => setOpenExamId((id) => (id === x.exam_id ? null : x.exam_id))}
                    >
                      {x.course} · {x.exam_date} ·{" "}
                      {x.days_left === 0 ? "today" : `${x.days_left} day${x.days_left === 1 ? "" : "s"} left`}
                    </button>
                    <button type="button" className="deck-btn exam-delete-btn" onClick={() => deleteExam(x)}>
                      Delete
                    </button>
                  </div>
                  {openExamId === x.exam_id && (
                    <ol className="exam-plan">
                      {(x.plan || []).map((day) => (
                        <li key={day.date} className="exam-plan-day">
                          <div className="exam-plan-date">{formatPlanDate(day.date)}</div>
                          <ul className="exam-plan-tasks">
                            {(day.tasks || []).map((t, i) => (
                              <li key={i}>{t}</li>
                            ))}
                          </ul>
                        </li>
                      ))}
                    </ol>
                  )}
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      <div ref={mockRef}>
        <h4 className="today-heading">Timed mock test</h4>
        {(phase === "setup" || phase === "loading") && (
          <form className="mock-form" onSubmit={startTest}>
            <label className="exam-field">
              <span>Course</span>
              <select
                className="mock-course-select"
                value={mockCourse}
                onChange={(e) => setMockCourse(e.target.value)}
                disabled={!courses.length}
              >
                {courses.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </label>
            <label className="exam-field">
              <span>Questions</span>
              <select
                className="mock-count-select"
                value={mockCount}
                onChange={(e) => setMockCount(Number(e.target.value))}
              >
                {COUNTS.map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </label>
            <label className="exam-field">
              <span>Minutes</span>
              <select
                className="mock-minutes-select"
                value={mockMinutes}
                onChange={(e) => setMockMinutes(Number(e.target.value))}
              >
                {MINUTES.map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="submit"
              className="study-action mock-start"
              disabled={phase === "loading" || !mockCourse}
            >
              {phase === "loading" ? "Writing questions…" : "Start mock test"}
            </button>
          </form>
        )}

        {phase === "running" && current && (
          <div className="mock-test">
            <div className="mock-test-head">
              <span className="flashcard-index">
                Question {idx + 1} / {items.length} · {answers.filter((a) => a != null).length} answered
              </span>
              <span className={`mock-timer${remaining < 60 ? " mock-timer--low" : ""}`} role="timer" aria-live="off">
                {formatClock(remaining)}
              </span>
            </div>
            <div className="mcq-q mock-question">
              <Markdown>{current.question}</Markdown>
            </div>
            <ul className="mcq-options mock-options">
              {(current.options || []).map((opt, i) => (
                <li key={i}>
                  <button
                    type="button"
                    className={answers[idx] === i ? "picked" : ""}
                    aria-pressed={answers[idx] === i}
                    onClick={() => pick(i)}
                  >
                    <Markdown inline>{opt}</Markdown>
                  </button>
                </li>
              ))}
            </ul>
            <div className="flashcard-nav">
              <button type="button" disabled={idx <= 0} onClick={() => setIdx((i) => i - 1)}>
                Prev
              </button>
              <button type="button" disabled={idx >= items.length - 1} onClick={() => setIdx((i) => i + 1)}>
                Next
              </button>
              <button type="button" className="mock-finish" onClick={() => finishTest(answers)}>
                Finish now
              </button>
            </div>
          </div>
        )}

        {phase === "done" && (
          <div className="mock-result">
            <p className="mcq-score-text mock-score" role="status">
              You got {score} / {items.length} in {minutesUsed} minute{minutesUsed === 1 ? "" : "s"}
            </p>
            <ol className="mock-review">
              {items.map((q, i) => {
                const mine = answers[i];
                const right = mine === q.answer_index;
                return (
                  <li key={i} className={`mock-review-item mock-review-item--${right ? "right" : "wrong"}`}>
                    <div className="mock-review-q">
                      <Markdown>{q.question}</Markdown>
                    </div>
                    <div className="mock-review-line">
                      {right ? "✓" : "✗"} Your answer:{" "}
                      {mine == null ? <em>no answer</em> : <Markdown inline>{q.options[mine]}</Markdown>}
                    </div>
                    {!right && (
                      <div className="mock-review-line mock-review-correct">
                        Correct: <Markdown inline>{q.options[q.answer_index] ?? ""}</Markdown>
                      </div>
                    )}
                    {q.explanation && (
                      <div className="mcq-explanation">
                        <Markdown>{q.explanation}</Markdown>
                      </div>
                    )}
                  </li>
                );
              })}
            </ol>
            <button
              type="button"
              className="study-action mock-again"
              onClick={() => {
                setPhase("setup");
                setItems([]);
                setAnswers([]);
              }}
            >
              New mock test
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
