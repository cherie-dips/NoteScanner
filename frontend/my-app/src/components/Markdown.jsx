import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";

const REMARK_PLUGINS = [remarkGfm, remarkMath];
const REHYPE_PLUGINS = [[rehypeKatex, { throwOnError: false, strict: false }]];

/**
 * AI answers often write math as \( … \) and \[ … \]; remark-math only understands $ … $ and $$ … $$.
 * Code spans and fenced blocks are left untouched.
 */
function normalizeMathDelimiters(text) {
  return String(text || "")
    .split(/(```[\s\S]*?```|`[^`\n]*`)/g)
    .map((part, i) => {
      if (i % 2 === 1) return part; // code
      return part
        .replace(/\\\[([\s\S]+?)\\\]/g, (_m, body) => `\n$$\n${body.trim()}\n$$\n`)
        // A line holding only $$…$$ is a display formula (remark-math wants the $$ on their own lines).
        .replace(/^[ \t]*\$\$([^\n]+?)\$\$[ \t]*$/gm, (_m, body) => `$$\n${body.trim()}\n$$`)
        .replace(/\\\(([\s\S]+?)\\\)/g, (_m, body) => `$${body.trim()}$`);
    })
    .join("");
}

// Inline mode (e.g. inside a button): paragraphs become plain text runs.
const INLINE_COMPONENTS = { p: ({ children }) => <>{children}</> };

/** Shared renderer for AI text: markdown, tables, and math formulas. */
export default function Markdown({ children, className = "", inline = false }) {
  const Wrapper = inline ? "span" : "div";
  return (
    <Wrapper className={`query-markdown${inline ? " query-markdown--inline" : ""} ${className}`.trim()}>
      <ReactMarkdown
        remarkPlugins={REMARK_PLUGINS}
        rehypePlugins={REHYPE_PLUGINS}
        components={inline ? INLINE_COMPONENTS : undefined}
      >
        {normalizeMathDelimiters(children)}
      </ReactMarkdown>
    </Wrapper>
  );
}
