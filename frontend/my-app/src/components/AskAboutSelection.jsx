import { useEffect, useState } from "react";

const MIN_CHARS = 3;
const MAX_CHARS = 2000;

/**
 * Floating "Ask about this" button that appears when the user selects text inside `containerRef`
 * (the Text view or a text preview). Clicking it hands the selected text to `onAsk`.
 */
export default function AskAboutSelection({ containerRef, onAsk }) {
  const [selection, setSelection] = useState(null); // { text, x, y }

  useEffect(() => {
    let frame = 0;
    const update = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const sel = window.getSelection();
        const container = containerRef.current;
        const text = sel ? sel.toString().trim() : "";
        if (
          !sel ||
          !container ||
          sel.rangeCount === 0 ||
          text.length < MIN_CHARS ||
          !container.contains(sel.anchorNode) ||
          !container.contains(sel.focusNode)
        ) {
          setSelection(null);
          return;
        }
        const rect = sel.getRangeAt(0).getBoundingClientRect();
        if (!rect || (!rect.width && !rect.height)) {
          setSelection(null);
          return;
        }
        setSelection({
          text: text.slice(0, MAX_CHARS),
          x: Math.min(Math.max(rect.left + rect.width / 2, 70), window.innerWidth - 70),
          y: rect.top < 48 ? rect.bottom + 8 : rect.top - 38,
        });
      });
    };
    const hide = () => setSelection(null);
    document.addEventListener("selectionchange", update);
    window.addEventListener("resize", hide);
    document.addEventListener("scroll", hide, true);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener("selectionchange", update);
      window.removeEventListener("resize", hide);
      document.removeEventListener("scroll", hide, true);
    };
  }, [containerRef]);

  if (!selection) return null;
  return (
    <button
      type="button"
      className="ask-selection-btn"
      style={{ left: selection.x, top: selection.y }}
      // Keep the selection alive while clicking.
      onMouseDown={(e) => e.preventDefault()}
      onClick={() => {
        onAsk?.(selection.text);
        window.getSelection()?.removeAllRanges();
        setSelection(null);
      }}
    >
      Ask about this
    </button>
  );
}
