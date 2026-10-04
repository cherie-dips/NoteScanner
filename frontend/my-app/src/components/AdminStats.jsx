import { useCallback, useEffect, useState } from "react";
import { apiRequest, errorText } from "../auth";

const TILES = [
  ["active_users", "Active students"],
  ["questions", "Questions"],
  ["uploads", "Uploads"],
  ["pages_read", "Pages read"],
  ["upload_failures", "Failed uploads"],
  ["reviews", "Flashcard reviews"],
  ["study_requests", "Study requests"],
];

function formatMoney(value, currency) {
  if (value == null || !Number.isFinite(Number(value))) return null;
  const n = Number(value);
  return `${n.toLocaleString(undefined, { maximumFractionDigits: n < 10 ? 2 : 0 })}${currency ? ` ${currency}` : ""}`;
}

function formatDate(epochSeconds) {
  if (!epochSeconds) return "";
  return new Date(epochSeconds * 1000).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Admin-only "Usage & feedback" modal: usage totals, per-day table, recent feedback. */
export default function AdminStats({ onClose }) {
  const [days, setDays] = useState(7);
  const [stats, setStats] = useState(null);
  const [feedback, setFeedback] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") onClose?.();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const load = useCallback(async (range) => {
    setLoading(true);
    setError("");
    try {
      const [s, f] = await Promise.all([
        apiRequest(`/admin/stats?days=${range}`),
        apiRequest("/admin/feedback?limit=50"),
      ]);
      setStats(s);
      setFeedback(Array.isArray(f.feedback) ? f.feedback : []);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(days);
  }, [load, days]);

  const totals = stats?.totals || {};
  const budget = stats?.budget || {};
  const currency = budget.currency || "";
  const helpful =
    totals.helpful_share == null ? "No ratings yet" : `${Math.round(Number(totals.helpful_share) * 100)}%`;
  const cost = formatMoney(totals.estimated_cost, currency);
  const monthCost = formatMoney(budget.month_cost, currency);
  const monthBudget = formatMoney(budget.monthly_budget, currency);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-card modal-card--wide admin-stats"
        role="dialog"
        aria-modal="true"
        aria-labelledby="admin-stats-title"
        onClick={(e) => e.stopPropagation()}
      >
        <button type="button" className="auth-close" onClick={onClose} aria-label="Close">
          ×
        </button>
        <h2 id="admin-stats-title" className="modal-title">Usage &amp; feedback</h2>

        <div className="admin-range" role="group" aria-label="Time range">
          {[7, 30].map((d) => (
            <button
              key={d}
              type="button"
              className={`admin-range-btn${days === d ? " active" : ""}`}
              data-days={d}
              aria-pressed={days === d}
              onClick={() => setDays(d)}
            >
              Last {d} days
            </button>
          ))}
        </div>

        {error && <p className="settings-status settings-status--error">{error}</p>}
        {loading && !stats && <p className="settings-text">Loading…</p>}

        {stats && (
          <>
            <div className="admin-tiles">
              {TILES.map(([key, label]) => (
                <div key={key} className="admin-tile" data-key={key}>
                  <div className="admin-tile-value">{Number(totals[key] ?? 0).toLocaleString()}</div>
                  <div className="admin-tile-label">{label}</div>
                </div>
              ))}
              <div className="admin-tile" data-key="helpful_share">
                <div className="admin-tile-value">{helpful}</div>
                <div className="admin-tile-label">
                  Helpful answers ({totals.ratings_up ?? 0} 👍 / {totals.ratings_down ?? 0} 👎)
                </div>
              </div>
              <div className="admin-tile" data-key="estimated_cost">
                <div className="admin-tile-value">{cost ?? "—"}</div>
                <div className="admin-tile-label">
                  {cost ? "Estimated AI cost" : "Estimated AI cost (set prices on the server to see it)"}
                </div>
              </div>
            </div>
            <p className="settings-text admin-budget">
              {monthBudget
                ? `Monthly AI budget: ${monthBudget}. Spent this month: ${monthCost ?? "unknown"}.`
                : "No monthly AI budget is set."}{" "}
              Tokens: {Number(totals.prompt_tokens ?? 0).toLocaleString()} in /{" "}
              {Number(totals.completion_tokens ?? 0).toLocaleString()} out.
            </p>

            <h3 className="settings-heading">Per day</h3>
            <div className="admin-table-wrap">
              <table className="admin-daily-table">
                <thead>
                  <tr>
                    <th scope="col">Day</th>
                    <th scope="col">Active</th>
                    <th scope="col">Questions</th>
                    <th scope="col">Uploads</th>
                    <th scope="col">Reviews</th>
                    <th scope="col">Study</th>
                  </tr>
                </thead>
                <tbody>
                  {(stats.daily || []).map((d) => (
                    <tr key={d.day}>
                      <td>{d.day}</td>
                      <td>{d.active_users ?? 0}</td>
                      <td>{d.questions ?? 0}</td>
                      <td>{d.uploads ?? 0}</td>
                      <td>{d.reviews ?? 0}</td>
                      <td>{d.study_requests ?? 0}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}

        <h3 className="settings-heading admin-feedback-heading">Recent feedback</h3>
        {feedback.length === 0 && !loading && <p className="settings-text">No feedback yet.</p>}
        <ul className="admin-feedback-list">
          {feedback.map((f, i) => (
            <li key={`${f.created_at}-${i}`} className={`admin-feedback-item admin-feedback-item--${f.rating || "none"}`}>
              <div className="admin-feedback-meta">
                <span>{formatDate(f.created_at)}</span>
                <span>{f.kind === "answer" ? (f.rating === "up" ? "👍" : f.rating === "down" ? "👎" : "") : "💬"}</span>
                <span className="admin-feedback-email">{f.user_email || ""}</span>
              </div>
              {f.comment && <div className="admin-feedback-comment">{f.comment}</div>}
              {f.question && <div className="admin-feedback-question">Q: {f.question}</div>}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
