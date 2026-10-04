import { apiRequest } from "./auth";
import { defaultDeckName } from "./studyUtils";

/**
 * Generate flashcards for one file and save them as a deck named after the file.
 * Returns { name, count }. Throws Error(readable message) on failure.
 */
export async function makeDeckFromFile(path) {
  const course = path.includes("/") ? path.split("/")[0] : "";
  const gen = await apiRequest("/study/generate", {
    method: "POST",
    form: {
      task: "flashcards",
      count: "8",
      opened_file_path: path,
      course_path: course,
      include_course_context: "false",
      focus_query: "important definitions and exam topics",
    },
  });
  const cards = (Array.isArray(gen.items) ? gen.items : []).filter(
    (c) => c && typeof c.front === "string" && typeof c.back === "string",
  );
  if (!cards.length) throw new Error("No flashcards could be made from this file.");
  const name = defaultDeckName(path);
  const deck = await apiRequest("/study/decks", {
    method: "POST",
    form: {
      name,
      source_path: path,
      cards: JSON.stringify(cards.map((c) => ({ front: c.front, back: c.back, source: c.source || "notes" }))),
    },
  });
  return { name: deck.name || name, count: deck.card_count ?? cards.length };
}

/** Record a finished MCQ set or mock test (feeds "weak topics" on the Today tab). Best effort. */
export async function recordMcqResult(path, correct, total) {
  if (!path || !total) return;
  try {
    await apiRequest("/study/mcq_results", {
      method: "POST",
      form: { path, correct: String(correct), total: String(total) },
    });
  } catch {
    /* stats are optional; never interrupt studying */
  }
}
