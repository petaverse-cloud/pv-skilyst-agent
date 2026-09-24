import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import Markdown from "./Markdown";

const html = (content: string, streaming = false) =>
  renderToStaticMarkup(<Markdown content={content} streaming={streaming} />);

// Real assistant answers, copied verbatim out of two sessions in ~/.skilyst/sessions
// (see the fixtures' headers). Rendering the shell's actual output is the point: a
// synthetic "hello **world**" fixture would not catch a broken table.
const answerTable = readFileSync(new URL("../__fixtures__/answer-table.md", import.meta.url), "utf8");
const answerCode = readFileSync(new URL("../__fixtures__/answer-code.md", import.meta.url), "utf8");

describe("Markdown", () => {
  it("renders headings, lists and emphasis as elements", () => {
    const out = html("## Result\n\n- one\n- two\n\n**bold** and *italic*");
    expect(out).toContain("<h2>Result</h2>");
    expect(out).toContain("<ul>");
    expect(out.match(/<li>/g)).toHaveLength(2);
    expect(out).toContain("<strong>bold</strong>");
    expect(out).toContain("<em>italic</em>");
  });

  it("renders a GFM table", () => {
    const out = html("| skill | node |\n|---|---|\n| doctor | none |");
    expect(out).toContain("<table>");
    expect(out).toContain("<th>skill</th>");
    expect(out).toContain("<td>doctor</td>");
    expect(out).toContain('class="md-table"');
  });

  it("renders GFM task lists with disabled checkboxes", () => {
    const out = html("- [x] done\n- [ ] todo");
    expect(out.match(/type="checkbox"/g)).toHaveLength(2);
    expect(out).toContain("checked");
  });

  it("keeps inline code inline and highlights fenced code", () => {
    const out = html("run `npm ci` first\n\n```python\nprint(1)\n```\n");
    expect(out).toContain('<code class="md-inline-code">npm ci</code>');
    expect(out).toContain('class="md-pre"');
    // Prism tokenizes into spans; a plain <code> would mean the highlighter was skipped.
    expect(out).toMatch(/class="token[^"]*"/);
    expect(out).not.toContain("```");
  });

  it("renders a fence without a language as a plain block", () => {
    const out = html("```\nplain text\n```");
    expect(out).toContain('<code class="md-code-plain">plain text</code>');
  });

  it("closes an unclosed fence while streaming", () => {
    const out = html("Answer so far:\n\n```python\nprint(1)", true);
    expect(out).toContain('class="md-pre"');
    expect(out).not.toContain("```");
  });

  it("drops a half-typed fence marker while streaming", () => {
    // A fence marker is only ever alone on its line; a stray backtick inside a
    // sentence is an inline span being typed and must stay.
    expect(html("Answer so far\n``", true)).toContain("Answer so far");
    expect(html("Answer so far\n``", true)).not.toContain("``");
    expect(html("run `npm", true)).toContain("`npm");
  });

  it("leaves a stored answer untouched when not streaming", () => {
    // A complete answer with a balanced fence must not be rewritten.
    const out = html("```\nkept\n```");
    expect(out).toContain("kept");
  });

  it("opens links outside the shell", () => {
    const out = html("[artifact](https://beehive-cdn.verse4.pet/x.mp4)");
    expect(out).toContain('target="_blank"');
    expect(out).toContain('rel="noreferrer noopener"');
  });

  it("does not execute or emit raw HTML from a model answer", () => {
    const out = html("<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>");
    expect(out).not.toContain("<script");
    expect(out).not.toContain("onerror");
  });

  it("strips a javascript: link", () => {
    const out = html("[click](javascript:alert(1))");
    expect(out).not.toContain("javascript:");
  });

  it("renders a real table answer out of a session", () => {
    const out = html(answerTable);
    expect(out).toContain("<table>");
    expect(out).toContain("skilyst/video-15s");
    expect(out).toContain("<h2>");
    expect(out).not.toContain("|---|");
  });

  it("renders a real code-block answer out of a session", () => {
    const out = html(answerCode);
    expect(out.match(/class="md-pre"/g)?.length ?? 0).toBeGreaterThanOrEqual(2);
    expect(out).not.toContain("```");
    expect(out).toContain("<strong>");
  });
});
