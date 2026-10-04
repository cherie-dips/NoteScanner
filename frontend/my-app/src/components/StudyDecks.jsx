import { useCallback, useEffect, useState } from "react";
import Markdown from "./Markdown";
import { API_BASE } from "../config";
import { apiRequest, authFetch, apiErrorMessage, downloadBlob, errorText } from "../auth";
import { shuffled } from "../studyUtils";

const GRADES = [
  { grade: "again", label: "Again", key: "1" },
  { grade: "hard", label: "Hard", key: "2" },
  { grade: "good", label: "Good", key: "3" },
  { grade: "easy", label: "Easy", key: "4" },
];

async function fetchCards(deckId, dueOnly, limit) {
  const data = await apiRequest(
    `/study/decks/${encodeURIComponent(deckId)}/cards?due_only=${dueOnly ? "true" : "false"}&limit=${limit}`,
  );
  return Array.isArray(data.cards) ? data.cards : [];
}

function filenameFromDisposition(header, fallback) {
  const h = header || "";
  const star = /filename\*=UTF-8''([^;]+)/i.exec(h);
  if (star) {
    try {
      return decodeURIComponent(star[1].trim());
    } catch {
      /* fall through */
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(h);
  return plain ? plain[1].trim() : fallback;
}

/** "Review" tab: saved flashcard decks, spaced-repetition review, practice, Anki export. */
export default function StudyDecks({ refreshKey = 0 }) {
  const [decks, setDecks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  // mode: list | review | practice | done
  const [mode, setMode] = useState("list");
  const [deck, setDeck] = useState(null);
  const [queue, setQueue] = useState([]);
  const [showAnswer, setShowAnswer] = useState(false);
  const [reviewedCount, setReviewedCount] = useState(0);
  const [grading, setGrading] = useState(false);
  const [practiceIdx, setPracticeIdx] = useState(0);

  const loadDecks = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const data = await apiRequest("/study/decks");
      setDecks(Array.isArray(data.decks) ? data.decks : []);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadDecks();
  }, [loadDecks, refreshKey]);

  const backToList = () => {
    setMode("list");
    setDeck(null);
    setQueue([]);
    setShowAnswer(false);
    void loadDecks();
  };

  const startReview = async (d) => {
    setError("");
    try {
      const cards = await fetchCards(d.deck_id, true, 50);
      setDeck(d);
      setReviewedCount(0);
      setShowAnswer(false);
      if (!cards.length) {
        setMode("done");
        return;
      }
      setQueue(cards);
      setMode("review");
    } catch (err) {
      setError(errorText(err));
    }
  };

  const startPractice = async (d) => {
    setError("");
    try {
      const cards = await fetchCards(d.deck_id, false, 200);
      if (!cards.length) {
        setError("This deck has no cards.");
        return;
      }
      setDeck(d);
      setQueue(shuffled(cards));
      setPracticeIdx(0);
      setShowAnswer(false);
      setMode("practice");
    } catch (err) {
      setError(errorText(err));
    }
  };

  const gradeCard = useCallback(
    async (grade) => {
      const card = queue[0];
      if (!card || grading) return;
      setGrading(true);
      setError("");
      try {
        await apiRequest(`/study/cards/${encodeURIComponent(card.card_id)}/review`, {
          method: "POST",
          form: { grade },
        });
        setReviewedCount((n) => n + 1);
        setShowAnswer(false);
        const rest = queue.slice(1);
        if (rest.length) {
          setQueue(rest);
        } else {
          // Cards graded "Again" may already be due again: keep going until nothing is due.
          const more = deck ? await fetchCards(deck.deck_id, true, 50) : [];
          setQueue(more);
          if (!more.length) setMode("done");
        }
      } catch (err) {
        setError(errorText(err));
      } finally {
        setGrading(false);
      }
    },
    [queue, grading, deck],
  );

  // Keyboard: Space shows the answer, 1–4 grade it.
  useEffect(() => {
    if (mode !== "review") return;
    const onKey = (e) => {
      const tag = (e.target?.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea") return;
      if (grading) return; // the previous grade is still saving; keys would hit the old card
      if (!showAnswer && (e.key === " " || e.key === "Enter")) {
        e.preventDefault();
        setShowAnswer(true);
        return;
      }
      if (showAnswer) {
        const g = GRADES.find((x) => x.key === e.key);
        if (g) {
          e.preventDefault();
          void gradeCard(g.grade);
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [mode, showAnswer, gradeCard, grading]);

  const exportDeck = async (d) => {
    setError("");
    try {
      const res = await authFetch(`${API_BASE}/study/decks/${encodeURIComponent(d.deck_id)}/export`);
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(apiErrorMessage(data, res));
      }
      const blob = await res.blob();
      const safeName = (d.name || "deck").replace(/[\\/:*?"<>|]+/g, "_");
      downloadBlob(blob, filenameFromDisposition(res.headers.get("Content-Disposition"), `${safeName}.csv`));
    } catch (err) {
      setError(errorText(err));
    }
  };

  const deleteDeck = async (d) => {
    if (!window.confirm(`Delete the deck "${d.name}" and its review history?`)) return;
    setError("");
    try {
      await apiRequest(`/study/decks/${encodeURIComponent(d.deck_id)}`, { method: "DELETE" });
      await loadDecks();
    } catch (err) {
      setError(errorText(err));
    }
  };

  if (mode === "review" && queue[0]) {
    const card = queue[0];
    return (
      <div className="study-section review-session">
        <div className="flashcard-index">
          {deck?.name} · {queue.length} left · {reviewedCount} reviewed
        </div>
        {error && <div className="study-error">{error}</div>}
        <div className="flashcard-face review-card">
          <Markdown>{card.front}</Markdown>
          {showAnswer && (
            <div className="review-answer">
              <Markdown>{card.back}</Markdown>
            </div>
          )}
        </div>
        {!showAnswer ? (
          <button
            type="button"
            className="study-action review-show-answer"
            onClick={() => setShowAnswer(true)}
            disabled={grading}
          >
            Show answer
          </button>
        ) : (
          <div className="review-grades" role="group" aria-label="How well did you remember?">
            {GRADES.map((g) => (
              <button
                key={g.grade}
                type="button"
                className={`review-grade-btn review-grade-btn--${g.grade}`}
                onClick={() => gradeCard(g.grade)}
                disabled={grading}
                title={`${g.label} (key ${g.key})`}
              >
                {g.label}
              </button>
            ))}
          </div>
        )}
        <p className="study-hint">Keys: Space = show answer, 1–4 = Again / Hard / Good / Easy.</p>
        <button type="button" className="study-link-btn" onClick={backToList}>
          ← Back to decks
        </button>
      </div>
    );
  }

  if (mode === "done") {
    return (
      <div className="study-section review-done">
        <p className="review-done-title">
          {reviewedCount ? `Nice work! You reviewed ${reviewedCount} card${reviewedCount === 1 ? "" : "s"}.` : "Nothing is due in this deck right now."}
        </p>
        <p className="study-hint">Cards come back when they're due, so a little every day works best.</p>
        <button type="button" className="study-action" onClick={backToList}>
          Back to decks
        </button>
      </div>
    );
  }

  if (mode === "practice" && queue.length) {
    const card = queue[practiceIdx];
    return (
      <div className="study-section practice-session">
        <div className="flashcard-index">
          {deck?.name} · practice {practiceIdx + 1} / {queue.length}
        </div>
        <div
          className="flashcard-face practice-card"
          role="button"
          tabIndex={0}
          onClick={() => setShowAnswer((s) => !s)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              setShowAnswer((s) => !s);
            }
          }}
          aria-label={showAnswer ? "Answer (click to see question)" : "Question (click to see answer)"}
        >
          <Markdown>{showAnswer ? card.back : card.front}</Markdown>
        </div>
        <div className="flashcard-nav">
          <button
            type="button"
            disabled={practiceIdx <= 0}
            onClick={() => { setPracticeIdx((i) => i - 1); setShowAnswer(false); }}
          >
            Prev
          </button>
          <button
            type="button"
            disabled={practiceIdx >= queue.length - 1}
            onClick={() => { setPracticeIdx((i) => i + 1); setShowAnswer(false); }}
          >
            Next
          </button>
        </div>
        <p className="study-hint">Practice doesn't change review dates.</p>
        <button type="button" className="study-link-btn" onClick={backToList}>
          ← Back to decks
        </button>
      </div>
    );
  }

  return (
    <div className="study-section deck-list-section">
      {error && <div className="study-error">{error}</div>}
      {loading ? (
        <p className="study-hint">Loading decks…</p>
      ) : decks.length === 0 ? (
        <p className="study-hint">
          No decks yet. Generate flashcards for a file, then click “Save as deck” to review them here
          with spaced repetition.
        </p>
      ) : (
        <ul className="deck-list">
          {decks.map((d) => (
            <li key={d.deck_id} className="deck-item">
              <div className="deck-item-name" title={d.source_path || d.name}>{d.name}</div>
              <div className="deck-item-meta">
                {d.card_count} card{d.card_count === 1 ? "" : "s"} ·{" "}
                <span className={d.due_count ? "deck-due" : ""}>{d.due_count} due</span>
              </div>
              <div className="deck-item-actions">
                <button
                  type="button"
                  className="deck-btn deck-review-btn"
                  onClick={() => startReview(d)}
                  disabled={!d.due_count}
                >
                  Review
                </button>
                <button type="button" className="deck-btn deck-practice-btn" onClick={() => startPractice(d)}>
                  Practice all (shuffled)
                </button>
                <button type="button" className="deck-btn deck-export-btn" onClick={() => exportDeck(d)}>
                  Export for Anki
                </button>
                <button
                  type="button"
                  className="deck-btn deck-delete-btn"
                  onClick={() => deleteDeck(d)}
                  aria-label={`Delete deck ${d.name}`}
                >
                  Delete
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
