import { useCallback, useEffect, useState } from "react";
import { apiRequest, errorText } from "../auth";
import { onShown } from "../embed";
import { looksLikeFile } from "../studyUtils";

function shortDay(day) {
  const d = new Date(`${day}T00:00:00`);
  if (Number.isNaN(d.getTime())) return day;
  return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
}

/**
 * "Today" tab: what to study now. Cards due, streak, upcoming exam, weak topics, recent activity.
 * GET /study/dashboard.
 */
export default function StudyToday({ onReviewNow, onPracticeFile, onMockTest }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setData(await apiRequest("/study/dashboard"));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Back on the Study AI tab: a deck may have been saved from Ask AI in the meantime.
  useEffect(() => onShown(() => void load()), [load]);

  if (loading && !data) return <p className="study-hint">Loading…</p>;
  if (error && !data) {
    return (
      <div className="study-error">
        {error}{" "}
        <button type="button" className="study-link-btn" onClick={load}>
          Retry
        </button>
      </div>
    );
  }
  if (!data) return null;

  const activity = Array.isArray(data.activity) ? data.activity : [];
  const maxActivity = Math.max(1, ...activity.map((a) => (a.reviews || 0) + (a.questions || 0)));
  const weak = Array.isArray(data.weak_topics) ? data.weak_topics : [];
  const exam = data.exam;

  return (
    <div className="study-section study-today">
      <div className="today-stats">
        <div className="today-stat today-due">
          <div className="today-stat-value">{data.due_today ?? 0}</div>
          <div className="today-stat-label">cards due</div>
        </div>
        <div className="today-stat today-reviewed">
          <div className="today-stat-value">{data.reviewed_today ?? 0}</div>
          <div className="today-stat-label">reviewed today</div>
        </div>
        <div className="today-stat today-streak">
          <div className="today-stat-value">
            {data.streak_days ?? 0}
            {(data.streak_days ?? 0) > 0 ? " 🔥" : ""}
          </div>
          <div className="today-stat-label">day streak</div>
        </div>
        <div className="today-stat today-decks">
          <div className="today-stat-value">{data.decks ?? 0}</div>
          <div className="today-stat-label">decks</div>
        </div>
      </div>

      {(data.due_today ?? 0) > 0 ? (
        <button type="button" className="study-action today-review-now" onClick={onReviewNow}>
          Review now
        </button>
      ) : (
        <p className="study-hint today-nothing-due">
          {(data.decks ?? 0) > 0
            ? "Nothing due right now. Nice work!"
            : "Save some flashcards as a deck to start daily reviews."}
        </p>
      )}

      {exam && (
        <div className="today-exam">
          <div className="today-exam-title">
            {exam.course} exam {exam.days_left === 0 ? "is today" : `in ${exam.days_left} day${exam.days_left === 1 ? "" : "s"}`}{" "}
            <span className="today-exam-date">({exam.exam_date})</span>
          </div>
          {Array.isArray(exam.today) && exam.today.length > 0 && (
            <ul className="today-exam-tasks">
              {exam.today.map((task, i) => (
                <li key={i}>{task}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      <h4 className="today-heading">Weak topics</h4>
      {weak.length === 0 ? (
        <p className="study-hint">Answer some MCQs or take a mock test to see where to focus.</p>
      ) : (
        <ul className="today-weak-list">
          {weak.map((w) => {
            const isFile = looksLikeFile(w.path);
            const pct = Math.round((Number(w.accuracy) || 0) * 100);
            return (
              <li key={w.path} className="today-weak-item">
                <div className="today-weak-name" title={w.path}>
                  {w.path.split("/").pop() || w.path}
                </div>
                <div className="today-weak-meta">
                  {pct}% right ({w.correct}/{w.total})
                </div>
                <button
                  type="button"
                  className="deck-btn today-practice-btn"
                  onClick={() => (isFile ? onPracticeFile?.(w.path) : onMockTest?.(w.path))}
                >
                  {isFile ? "Practice" : "Mock test"}
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {activity.length > 0 && (
        <>
          <h4 className="today-heading">Last {activity.length} days</h4>
          <div className="today-activity" role="img" aria-label="Study activity per day">
            {activity.map((a) => {
              const total = (a.reviews || 0) + (a.questions || 0);
              const level = total === 0 ? 0 : Math.min(4, Math.ceil((total / maxActivity) * 4));
              return (
                <span
                  key={a.day}
                  className={`today-activity-day today-activity-day--${level}`}
                  title={`${shortDay(a.day)}: ${a.reviews || 0} reviews, ${a.questions || 0} questions`}
                />
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
