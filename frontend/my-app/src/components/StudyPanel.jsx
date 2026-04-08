import { useState, useCallback } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  useNodesState,
  useEdgesState,
  ReactFlowProvider,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { API_BASE } from "../config";
import { authFetch } from "../auth";

function graphToFlow(graph) {
  const rawNodes = graph?.nodes || [];
  const nodes = rawNodes.map((n, i) => ({
    id: String(n.id),
    position: { x: (i % 6) * 140, y: Math.floor(i / 6) * 100 },
    data: { label: n.label || n.id },
  }));
  const edges = (graph?.edges || []).map((e, i) => ({
    id: `e${i}`,
    source: String(e.source),
    target: String(e.target),
    label: e.label || "",
  }));
  return { initialNodes: nodes, initialEdges: edges };
}

export default function StudyPanel({
  coursesCsv = "",
  activeFilePath = "",
  activeCourseFolder = "",
}) {
  const [tab, setTab] = useState("flashcards");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [cards, setCards] = useState([]);
  const [cardIdx, setCardIdx] = useState(0);
  const [showBack, setShowBack] = useState(false);
  const [mcq, setMcq] = useState([]);
  const [mcqIdx, setMcqIdx] = useState(0);
  const [picked, setPicked] = useState(null);
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const hasActiveFile = !!(activeFilePath || "").trim();

  const runFlashcards = useCallback(async () => {
    setLoading(true);
    setError("");
    setCards([]);
    setCardIdx(0);
    setShowBack(false);
    try {
      const fd = new FormData();
      fd.append("task", "flashcards");
      fd.append("count", "8");
      fd.append("courses", coursesCsv);
      fd.append("opened_file_path", activeFilePath || "");
      fd.append("course_path", activeCourseFolder || "");
      fd.append("include_course_context", "true");
      fd.append("focus_query", "important definitions and exam topics");
      const res = await authFetch(`${API_BASE}/study/generate`, { method: "POST", body: fd });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || "Failed");
      const raw = Array.isArray(data.items) ? data.items : [];
      const items = raw.filter(
        (x) => x && typeof x.front === "string" && typeof x.back === "string"
      );
      if (!items.length) throw new Error("No flashcards returned for this file.");
      setCards(items);
      setCardIdx(0);
      setShowBack(false);
    } catch (e) {
      setError(e.message || "Failed");
      setCards([]);
    } finally {
      setLoading(false);
    }
  }, [coursesCsv, activeFilePath, activeCourseFolder]);

  const runMcq = useCallback(async () => {
    setLoading(true);
    setError("");
    setMcq([]);
    setMcqIdx(0);
    setPicked(null);
    try {
      const fd = new FormData();
      fd.append("task", "mcq");
      fd.append("count", "5");
      fd.append("courses", coursesCsv);
      fd.append("opened_file_path", activeFilePath || "");
      fd.append("course_path", activeCourseFolder || "");
      fd.append("include_course_context", "true");
      fd.append("focus_query", "practice exam style");
      const res = await authFetch(`${API_BASE}/study/generate`, { method: "POST", body: fd });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || "Failed");
      const raw = Array.isArray(data.items) ? data.items : [];
      const items = raw.filter(
        (x) =>
          x &&
          typeof x.question === "string" &&
          Array.isArray(x.options) &&
          x.options.length >= 2
      );
      if (!items.length) throw new Error("No MCQs returned for this file.");
      setMcq(items);
      setMcqIdx(0);
      setPicked(null);
    } catch (e) {
      setError(e.message || "Failed");
      setMcq([]);
    } finally {
      setLoading(false);
    }
  }, [coursesCsv, activeFilePath, activeCourseFolder]);

  const runMindmap = useCallback(async () => {
    setLoading(true);
    setError("");
    setNodes([]);
    setEdges([]);
    try {
      const fd = new FormData();
      fd.append("courses", coursesCsv);
      fd.append("opened_file_path", activeFilePath || "");
      fd.append("course_path", activeCourseFolder || "");
      fd.append("include_course_context", "true");
      fd.append("focus_query", "concept relationships");
      const res = await authFetch(`${API_BASE}/study/mindmap`, { method: "POST", body: fd });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || "Failed");
      const nodesRaw = Array.isArray(data?.graph?.nodes) ? data.graph.nodes : [];
      if (!nodesRaw.length) throw new Error("No mind map returned for this file.");
      const graph = {
        ...data.graph,
        nodes: nodesRaw.map((n) => {
          if (!n || typeof n.label !== "string") return n;
          const sq = typeof n.source_quote === "string" ? n.source_quote.trim() : "";
          const label = n.label.trim();
          return {
            ...n,
            source_quote: sq.length >= 8 ? sq : label.slice(0, 220),
          };
        }),
      };
      const { initialNodes, initialEdges } = graphToFlow(graph);
      setNodes(initialNodes);
      setEdges(initialEdges);
    } catch (e) {
      setError(e.message || "Failed");
      setNodes([]);
      setEdges([]);
    } finally {
      setLoading(false);
    }
  }, [coursesCsv, activeFilePath, activeCourseFolder, setNodes, setEdges]);

  return (
    <ReactFlowProvider>
    <div className="study-panel">
      <div className="study-tabs">
        <button type="button" className={tab === "flashcards" ? "active" : ""} onClick={() => setTab("flashcards")}>
          Flashcards
        </button>
        <button type="button" className={tab === "mcq" ? "active" : ""} onClick={() => setTab("mcq")}>
          Mock MCQ
        </button>
        <button type="button" className={tab === "map" ? "active" : ""} onClick={() => setTab("map")}>
          Mind map
        </button>
      </div>
      {error && <div className="study-error">{error}</div>}
      {tab === "flashcards" && (
        <div className="study-section">
          <button type="button" className="study-action" disabled={loading || !hasActiveFile} onClick={runFlashcards}>
            {loading ? "…" : "Generate flashcards"}
          </button>
          {cards.length > 0 && (
            <div className="flashcard">
              <div className="flashcard-index">
                {cardIdx + 1} / {cards.length}
              </div>
              <button type="button" className="flashcard-face" onClick={() => setShowBack((s) => !s)}>
                {showBack ? cards[cardIdx]?.back : cards[cardIdx]?.front}
              </button>
              <div className="flashcard-nav">
                <button type="button" disabled={cardIdx <= 0} onClick={() => { setCardIdx((i) => i - 1); setShowBack(false); }}>
                  Prev
                </button>
                <button type="button" disabled={cardIdx >= cards.length - 1} onClick={() => { setCardIdx((i) => i + 1); setShowBack(false); }}>
                  Next
                </button>
              </div>
            </div>
          )}
        </div>
      )}
      {tab === "mcq" && (
        <div className="study-section">
          <button type="button" className="study-action" disabled={loading || !hasActiveFile} onClick={runMcq}>
            {loading ? "…" : "Generate MCQ"}
          </button>
          {mcq.length > 0 && (
            <div className="mcq-block">
              <p className="mcq-q">{mcq[mcqIdx]?.question}</p>
              <ul className="mcq-options">
                {(mcq[mcqIdx]?.options || []).map((opt, i) => (
                  <li key={i}>
                    <button type="button" className={picked === i ? "picked" : ""} onClick={() => setPicked(i)}>
                      {opt}
                    </button>
                  </li>
                ))}
              </ul>
              {picked != null && (
                <p className="mcq-ans">
                  {picked === mcq[mcqIdx]?.answer_index ? "Correct." : `Answer: option ${(mcq[mcqIdx]?.answer_index ?? 0) + 1}`}
                </p>
              )}
              <div className="flashcard-nav">
                <button type="button" disabled={mcqIdx <= 0} onClick={() => { setMcqIdx((i) => i - 1); setPicked(null); }}>
                  Prev
                </button>
                <button type="button" disabled={mcqIdx >= mcq.length - 1} onClick={() => { setMcqIdx((i) => i + 1); setPicked(null); }}>
                  Next
                </button>
              </div>
            </div>
          )}
        </div>
      )}
      {tab === "map" && (
        <div className="study-section study-map-wrap">
          <button type="button" className="study-action" disabled={loading || !hasActiveFile} onClick={runMindmap}>
            {loading ? "…" : "Build mind map"}
          </button>
          <div className="react-flow-wrap">
            <ReactFlow nodes={nodes} edges={edges} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} fitView>
              <Background />
              <Controls />
            </ReactFlow>
          </div>
        </div>
      )}
    </div>
    </ReactFlowProvider>
  );
}
