import { getUserId } from "./auth";

const HISTORY_LIMIT = 100;
const SOURCE_PREVIEW_CHARS = 300;

/** localStorage key for the signed-in user's saved chat on this device ("" if unknown). */
export function chatHistoryKey() {
  const uid = getUserId();
  return uid ? `notescanner-chat-v1-${uid}` : "";
}

/** Saved chat for this user on this device: { chatSessionId, messages }. */
export function loadHistory() {
  const key = chatHistoryKey();
  if (!key) return null;
  try {
    const data = JSON.parse(localStorage.getItem(key) || "null");
    if (!data || !Array.isArray(data.messages)) return null;
    return {
      chatSessionId: typeof data.chatSessionId === "string" ? data.chatSessionId : null,
      messages: data.messages.filter((m) => m && (m.role === "user" || m.role === "assistant")),
    };
  } catch {
    return null;
  }
}

export function saveHistory(chatSessionId, messages) {
  const key = chatHistoryKey();
  if (!key) return;
  const trimmed = messages.slice(-HISTORY_LIMIT).map((m) => ({
    id: m.id,
    role: m.role,
    content: m.content || "",
    // Answer ratings and the context they need are kept so a rated answer stays rated.
    question: m.question || undefined,
    highlight: m.highlight || undefined,
    selection_stage: m.selection_stage || undefined,
    rating: m.rating || undefined,
    feedbackState: m.feedbackState === "sent" ? "sent" : undefined,
    source_documents: (m.source_documents || []).map((d) => ({
      content: (d.content || "").slice(0, SOURCE_PREVIEW_CHARS),
      metadata: d.metadata || {},
    })),
  }));
  try {
    localStorage.setItem(key, JSON.stringify({ chatSessionId, messages: trimmed }));
  } catch {
    /* storage full or blocked: history is a convenience */
  }
}

export function clearHistory() {
  const key = chatHistoryKey();
  if (!key) return;
  try {
    localStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}
