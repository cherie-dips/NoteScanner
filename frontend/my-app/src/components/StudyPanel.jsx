import { useState, useCallback, useEffect, useRef } from "react";
import Markdown from "./Markdown";
import StudyDecks from "./StudyDecks";
import StudyToday from "./StudyToday";
import ExamMode from "./ExamMode";
import { API_BASE } from "../config";
import { authFetch, apiErrorMessage, apiRequest, errorText } from "../auth";
import { defaultDeckName, shuffled } from "../studyUtils";
import { recordMcqResult } from "../studyApi";

const TABS = [
  ["today", "Today"],
  ["flashcards", "Flashcards"],
  ["mcq", "Mock MCQ"],
  ["summary", "Summary"],
  ["review", "Review"],
  ["exam", "Exam"],
];
// Tabs that work without an open file.
const FILELESS_TABS = new Set(["today", "review", "exam"]);

export default function StudyPanel({
  activeFilePath = "",
  activeCourseFolder = "",
  onOpenFile,
}) {
  const [tab, setTab] = useState("today");
  const [examPreset, setExamPreset] = useState({ course: "", key: 0 });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [cards, setCards] = useState([]);
  const [cardIdx, setCardIdx] = useState(0);
  const [showBack, setShowBack] = useState(false);
  const [deckForm, setDeckForm] = useState(null); // null | { name }
  const [deckSaving, setDeckSaving] = useState(false);
  const [deckNotice, setDeckNotice] = useState("");
  const [decksVersion, setDecksVersion] = useState(0);
  const [mcq, setMcq] = useState([]);
  const [mcqIdx, setMcqIdx] = useState(0);
  const [mcqAnswers, setMcqAnswers] = useState([]);
  const [summary, setSummary] = useState("");
  const hasActiveFile = !!(activeFilePath || "").trim();
  // Which file the current MCQ set came from, and whether its result was already recorded.
  const mcqSourceRef = useRef("");
  const mcqRecordedRef = useRef(true);
  // Bumped whenever the open file changes, so late results for the previous file are ignored.
  const requestIdRef = useRef(0);

  useEffect(() => {
    requestIdRef.current += 1;
    setLoading(false);
    setError("");
    setCards([]);
    setCardIdx(0);
    setShowBack(false);
    setDeckForm(null);
    setDeckNotice("");
    setMcq([]);
    setMcqIdx(0);
    setMcqAnswers([]);
    setSummary("");
  }, [activeFilePath]);

  const studyForm = useCallback(
    (extra) => {
      const fd = new FormData();
      fd.append("opened_file_path", activeFilePath || "");
      fd.append("course_path", activeCourseFolder || "");
      fd.append("include_course_context", "true");
      for (const [k, v] of Object.entries(extra)) fd.append(k, v);
      return fd;
    },
    [activeFilePath, activeCourseFolder],
  );

  const runFlashcards = useCallback(async () => {
    const reqId = ++requestIdRef.current;
    setLoading(true);
    setError("");
    setCards([]);
    setCardIdx(0);
    setShowBack(false);
    setDeckForm(null);
    setDeckNotice("");
    try {
      const fd = studyForm({
        task: "flashcards",
        count: "8",
        focus_query: "important definitions and exam topics",
      });
      const res = await authFetch(`${API_BASE}/study/generate`, { method: "POST", body: fd });
      const data = await res.json().catch(() => ({}));
      if (reqId !== requestIdRef.current) return;
      if (!res.ok) throw new Error(apiErrorMessage(data, res));
      const raw = Array.isArray(data.items) ? data.items : [];
      const items = raw.filter(
        (x) => x && typeof x.front === "string" && typeof x.back === "string"
      );
      if (!items.length) throw new Error("No flashcards returned for this file.");
      setCards(items);
      setCardIdx(0);
      setShowBack(false);
    } catch (e) {
      if (reqId !== requestIdRef.current) return;
      setError(errorText(e));
      setCards([]);
    } finally {
      if (reqId === requestIdRef.current) setLoading(false);
    }
  }, [studyForm]);

  const runMcq = useCallback(async () => {
    const reqId = ++requestIdRef.current;
    setLoading(true);
    setError("");
    setMcq([]);
    setMcqIdx(0);
    setMcqAnswers([]);
    try {
      const fd = studyForm({ task: "mcq", count: "5", focus_query: "practice exam style" });
      const res = await authFetch(`${API_BASE}/study/generate`, { method: "POST", body: fd });
      const data = await res.json().catch(() => ({}));
      if (reqId !== requestIdRef.current) return;
      if (!res.ok) throw new Error(apiErrorMessage(data, res));
      const raw = Array.isArray(data.items) ? data.items : [];
      const items = raw.filter(
        (x) =>
          x &&
          typeof x.question === "string" &&
          Array.isArray(x.options) &&
          x.options.length >= 2
      );
      if (!items.length) throw new Error("No MCQs returned for this file.");
      mcqSourceRef.current = activeFilePath;
      mcqRecordedRef.current = false;
      setMcq(items);
      setMcqIdx(0);
      setMcqAnswers(items.map(() => null));
    } catch (e) {
      if (reqId !== requestIdRef.current) return;
      setError(errorText(e));
      setMcq([]);
    } finally {
      if (reqId === requestIdRef.current) setLoading(false);
    }
  }, [studyForm, activeFilePath]);

  const runSummary = useCallback(async () => {
    const reqId = ++requestIdRef.current;
    setLoading(true);
    setError("");
    setSummary("");
    try {
      const fd = studyForm({ focus_query: "comprehensive topic summary" });
      const res = await authFetch(`${API_BASE}/study/summary`, { method: "POST", body: fd });
      const data = await res.json().catch(() => ({}));
      if (reqId !== requestIdRef.current) return;
      if (!res.ok) throw new Error(apiErrorMessage(data, res));
      if (!data.summary) throw new Error("No summary returned for this file.");
      setSummary(data.summary);
    } catch (e) {
      if (reqId !== requestIdRef.current) return;
      setError(errorText(e));
      setSummary("");
    } finally {
      if (reqId === requestIdRef.current) setLoading(false);
    }
  }, [studyForm]);

  const shuffleCards = () => {
    setCards((prev) => shuffled(prev));
    setCardIdx(0);
    setShowBack(false);
  };

  const saveDeck = async (e) => {
    e.preventDefault();
    const name = (deckForm?.name || "").trim();
    if (!name || !cards.length) return;
    setDeckSaving(true);
    setError("");
    try {
      const data = await apiRequest("/study/decks", {
        method: "POST",
        form: {
          name,
          source_path: activeFilePath || "",
          cards: JSON.stringify(
            cards.map((c) => ({ front: c.front, back: c.back, source: c.source || "notes" })),
          ),
        },
      });
      setDeckForm(null);
      setDeckNotice(
        `Saved “${data.name || name}” (${data.card_count ?? cards.length} cards). Review it in the Review tab.`,
      );
      setDecksVersion((v) => v + 1);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setDeckSaving(false);
    }
  };

  const pickAnswer = (optionIdx) => {
    setMcqAnswers((prev) => {
      if (prev[mcqIdx] != null) return prev; // one try per question
      const next = [...prev];
      next[mcqIdx] = optionIdx;
      return next;
    });
  };

  const currentMcq = mcq[mcqIdx];
  const picked = mcqAnswers[mcqIdx] ?? null;
  const answeredCount = mcqAnswers.filter((a) => a != null).length;
  const allAnswered = mcq.length > 0 && answeredCount === mcq.length;
  const score = mcq.reduce((n, q, i) => n + (mcqAnswers[i] === q.answer_index ? 1 : 0), 0);

  // Record the first full attempt of each MCQ set (feeds "weak topics" on the Today tab).
  useEffect(() => {
    if (!allAnswered || mcqRecordedRef.current) return;
    mcqRecordedRef.current = true;
    void recordMcqResult(mcqSourceRef.current, score, mcq.length);
  }, [allAnswered, score, mcq.length]);

  const practiceFile = (path) => {
    onOpenFile?.(path);
    setTab("mcq");
  };

  const mockTestFor = (course) => {
    setExamPreset((p) => ({ course, key: p.key + 1 }));
    setTab("exam");
  };

  return (
    <div className="study-panel">
      <div className="study-tabs" role="tablist">
        {TABS.map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            className={`study-tab study-tab--${key}${tab === key ? " active" : ""}`}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>
      {error && !FILELESS_TABS.has(tab) && <div className="study-error">{error}</div>}
      {!hasActiveFile && !FILELESS_TABS.has(tab) && (
        <p className="study-hint">Open a file to generate study material from it.</p>
      )}

      {tab === "today" && (
        <StudyToday
          onReviewNow={() => setTab("review")}
          onPracticeFile={practiceFile}
          onMockTest={mockTestFor}
        />
      )}

      {tab === "flashcards" && (
        <div className="study-section">
          <button type="button" className="study-action" disabled={loading || !hasActiveFile} onClick={runFlashcards}>
            {loading ? "…" : "Generate flashcards"}
          </button>
          {cards.length > 0 && (
            <div className="flashcard">
              <div className="flashcard-index">
                {cardIdx + 1} / {cards.length}
                {cards[cardIdx]?.source === "subject_knowledge" && (
                  <span className="flashcard-badge">📖 Beyond notes</span>
                )}
              </div>
              <div
                className="flashcard-face"
                role="button"
                tabIndex={0}
                aria-label={showBack ? "Answer (click to see question)" : "Question (click to see answer)"}
                onClick={() => setShowBack((s) => !s)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    setShowBack((s) => !s);
                  }
                }}
              >
                <Markdown>{showBack ? cards[cardIdx]?.back : cards[cardIdx]?.front}</Markdown>
              </div>
              <div className="flashcard-nav">
                <button type="button" disabled={cardIdx <= 0} onClick={() => { setCardIdx((i) => i - 1); setShowBack(false); }}>
                  Prev
                </button>
                <button type="button" disabled={cardIdx >= cards.length - 1} onClick={() => { setCardIdx((i) => i + 1); setShowBack(false); }}>
                  Next
                </button>
                <button type="button" className="flashcard-shuffle" onClick={shuffleCards}>
                  Shuffle
                </button>
              </div>
              {deckForm ? (
                <form className="deck-save-form" onSubmit={saveDeck}>
                  <input
                    type="text"
                    className="deck-save-input"
                    value={deckForm.name}
                    onChange={(e) => setDeckForm({ name: e.target.value })}
                    aria-label="Deck name"
                    maxLength={120}
                    autoFocus
                  />
                  <button type="submit" className="study-action deck-save-confirm" disabled={deckSaving || !deckForm.name.trim()}>
                    {deckSaving ? "Saving…" : "Save"}
                  </button>
                  <button type="button" className="study-link-btn" onClick={() => setDeckForm(null)}>
                    Cancel
                  </button>
                </form>
              ) : (
                <button
                  type="button"
                  className="study-action deck-save-btn"
                  onClick={() => {
                    setDeckNotice("");
                    setDeckForm({ name: defaultDeckName(activeFilePath) });
                  }}
                >
                  Save as deck
                </button>
              )}
              {deckNotice && <p className="study-notice">{deckNotice}</p>}
            </div>
          )}
        </div>
      )}

      {tab === "mcq" && (
        <div className="study-section">
          <button type="button" className="study-action" disabled={loading || !hasActiveFile} onClick={runMcq}>
            {loading ? "…" : "Generate MCQ"}
          </button>
          {currentMcq && (
            <div className="mcq-block">
              <div className="flashcard-index">
                Question {mcqIdx + 1} / {mcq.length} · {answeredCount} answered
              </div>
              <div className="mcq-q">
                <Markdown>{currentMcq.question}</Markdown>
                {currentMcq.source === "subject_knowledge" && (
                  <span className="mcq-badge">📖 Beyond notes</span>
                )}
              </div>
              <ul className="mcq-options">
                {(currentMcq.options || []).map((opt, i) => {
                  const isCorrect = i === currentMcq.answer_index;
                  const cls = [
                    picked === i ? "picked" : "",
                    picked != null && isCorrect ? "correct" : "",
                    picked === i && !isCorrect ? "wrong" : "",
                  ].filter(Boolean).join(" ");
                  return (
                    <li key={i}>
                      <button
                        type="button"
                        className={cls}
                        onClick={() => pickAnswer(i)}
                        disabled={picked != null}
                        aria-pressed={picked === i}
                      >
                        <Markdown inline>{opt}</Markdown>
                      </button>
                    </li>
                  );
                })}
              </ul>
              {picked != null && (
                <div className="mcq-feedback">
                  <p className={`mcq-ans${picked === currentMcq.answer_index ? "" : " mcq-ans--wrong"}`}>
                    {picked === currentMcq.answer_index
                      ? "✓ Correct!"
                      : `✗ The answer is option ${(currentMcq.answer_index ?? 0) + 1}.`}
                  </p>
                  {currentMcq.explanation && (
                    <div className="mcq-explanation">
                      <Markdown>{currentMcq.explanation}</Markdown>
                    </div>
                  )}
                </div>
              )}
              <div className="flashcard-nav">
                <button type="button" disabled={mcqIdx <= 0} onClick={() => setMcqIdx((i) => i - 1)}>
                  Prev
                </button>
                <button type="button" disabled={mcqIdx >= mcq.length - 1} onClick={() => setMcqIdx((i) => i + 1)}>
                  Next
                </button>
              </div>
              {allAnswered && (
                <div className="mcq-score" role="status">
                  <p className="mcq-score-text">
                    You got {score} / {mcq.length}
                  </p>
                  <div className="flashcard-nav">
                    <button
                      type="button"
                      className="mcq-retry"
                      onClick={() => {
                        setMcqAnswers(mcq.map(() => null));
                        setMcqIdx(0);
                      }}
                    >
                      Try again
                    </button>
                    <button type="button" className="mcq-new" onClick={runMcq} disabled={loading}>
                      New questions
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {tab === "summary" && (
        <div className="study-section">
          <button type="button" className="study-action" disabled={loading || !hasActiveFile} onClick={runSummary}>
            {loading ? "…" : "Generate summary"}
          </button>
          {summary && (
            <div className="study-summary">
              <Markdown>{summary}</Markdown>
            </div>
          )}
        </div>
      )}

      {tab === "review" && <StudyDecks refreshKey={decksVersion} />}

      {tab === "exam" && (
        <ExamMode
          activeCourseFolder={activeCourseFolder}
          presetCourse={examPreset.course}
          presetKey={examPreset.key}
        />
      )}
    </div>
  );
}
