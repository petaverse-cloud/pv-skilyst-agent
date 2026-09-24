/**
 * Markdown rendering for chat bubbles.
 *
 * Same stack as the Pawly web app (`react-markdown` + `remark-gfm`, GFM tables and
 * task lists) plus a Prism highlighter, registered language by language so the bundle
 * only carries the languages a Skilyst answer plausibly contains. Raw HTML is *not*
 * enabled and the tree is sanitized: the text being rendered comes from a model.
 *
 * While a message is still streaming it is normalized first (`normalizeStreamingMarkdown`),
 * so a half-typed fence marker does not leak into the bubble and an unclosed code block
 * is closed instead of swallowing the rest of the answer.
 */
import { memo, useMemo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import { PrismLight as SyntaxHighlighter } from "react-syntax-highlighter";
import bash from "react-syntax-highlighter/dist/esm/languages/prism/bash";
import diff from "react-syntax-highlighter/dist/esm/languages/prism/diff";
import docker from "react-syntax-highlighter/dist/esm/languages/prism/docker";
import go from "react-syntax-highlighter/dist/esm/languages/prism/go";
import ini from "react-syntax-highlighter/dist/esm/languages/prism/ini";
import javascript from "react-syntax-highlighter/dist/esm/languages/prism/javascript";
import json from "react-syntax-highlighter/dist/esm/languages/prism/json";
import markdown from "react-syntax-highlighter/dist/esm/languages/prism/markdown";
import python from "react-syntax-highlighter/dist/esm/languages/prism/python";
import sql from "react-syntax-highlighter/dist/esm/languages/prism/sql";
import typescript from "react-syntax-highlighter/dist/esm/languages/prism/typescript";
import yaml from "react-syntax-highlighter/dist/esm/languages/prism/yaml";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { normalizeStreamingMarkdown } from "../markdown";

// Registered explicitly (PrismLight): an unregistered language still renders, as
// plain code, so a ```` ```whatever ```` fence in an answer is never a hard failure.
const LANGUAGES: Record<string, unknown> = {
  bash,
  diff,
  docker,
  go,
  ini,
  javascript,
  json,
  markdown,
  python,
  sql,
  typescript,
  yaml,
};
for (const [name, language] of Object.entries(LANGUAGES)) {
  SyntaxHighlighter.registerLanguage(name, language);
}

const CODE_STYLE = {
  margin: 0,
  borderRadius: 6,
  fontSize: 12.5,
  lineHeight: 1.55,
  background: "var(--mantine-color-dark-8)",
};

const components: Components = {
  // A fenced block that carries a language is replaced by the highlighter, which
  // brings its own block element -- so `pre` becomes a plain wrapper instead of a
  // second box around it.
  pre: ({ children }) => <div className="md-pre">{children}</div>,
  code: ({ className, children }) => {
    const language = /language-([\w+#.-]+)/.exec(className ?? "")?.[1];
    const text = String(children);
    // Inside a fence the literal always ends with the newline before the closing
    // marker; an inline span never does.
    const block = Boolean(language) || text.endsWith("\n");
    if (!block) {
      return <code className="md-inline-code">{children}</code>;
    }
    const body = text.replace(/\n$/, "");
    if (!language) {
      return <code className="md-code-plain">{body}</code>;
    }
    return (
      <SyntaxHighlighter language={language} style={oneDark} PreTag="div" customStyle={CODE_STYLE} codeTagProps={{ style: CODE_STYLE }}>
        {body}
      </SyntaxHighlighter>
    );
  },
  table: ({ children }) => (
    <div className="md-table">
      <table>{children}</table>
    </div>
  ),
  // Links open outside the webview: navigating the shell away from the app on a
  // click would leave the user staring at a web page inside the desktop window.
  a: ({ children, href }) => (
    <a href={href} target="_blank" rel="noreferrer noopener">
      {children}
    </a>
  ),
  input: ({ type, checked }) =>
    type === "checkbox" ? <input type="checkbox" checked={Boolean(checked)} readOnly /> : null,
};

function Markdown({ content, streaming = false }: { content: string; streaming?: boolean }) {
  const text = useMemo(
    () => (streaming ? normalizeStreamingMarkdown(content) : content),
    [content, streaming],
  );
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  );
}

// The transcript re-renders on every streamed token; a stored message must not.
export default memo(Markdown);
