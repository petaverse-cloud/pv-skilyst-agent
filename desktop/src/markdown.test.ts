import { describe, expect, it } from "vitest";
import { fenceState, normalizeStreamingMarkdown } from "./markdown";

describe("fenceState", () => {
  it("sees a balanced document as closed", () => {
    expect(fenceState("text\n```js\nlet a = 1;\n```\nmore").open).toBe(null);
  });

  it("reports the fence character that is still open", () => {
    expect(fenceState("intro\n```python\nprint(1)").open).toBe("`");
    expect(fenceState("intro\n~~~\nplain").open).toBe("~");
  });

  it("flags a fence marker that is still being typed", () => {
    expect(fenceState("answer so far\n`")).toEqual({ open: null, partial: true });
    expect(fenceState("answer so far\n``")).toEqual({ open: null, partial: true });
    expect(fenceState("answer so far `x`")).toEqual({ open: null, partial: false });
  });

  it("ignores backticks that are not at the start of a line", () => {
    expect(fenceState("use ``` inside a sentence").open).toBe(null);
  });
});

describe("normalizeStreamingMarkdown", () => {
  it("returns complete markdown untouched", () => {
    const complete = "## Result\n\n- one\n- two\n\n```bash\nls\n```\n";
    expect(normalizeStreamingMarkdown(complete)).toBe(complete);
  });

  it("closes a code block whose closing fence has not arrived", () => {
    expect(normalizeStreamingMarkdown("```python\nprint(1)")).toBe("```python\nprint(1)\n```");
  });

  it("closes a fence that has only just opened", () => {
    expect(normalizeStreamingMarkdown("text\n```")).toBe("text\n```\n```");
  });

  it("drops a half-typed fence marker instead of rendering it as text", () => {
    // A fence only ever starts a line, so the marker being typed is a line of its own.
    expect(normalizeStreamingMarkdown("an answer\n``")).toBe("an answer");
    expect(normalizeStreamingMarkdown("an answer\n`")).toBe("an answer");
  });

  it("keeps inline code spans intact", () => {
    expect(normalizeStreamingMarkdown("use `npm ci` here")).toBe("use `npm ci` here");
  });

  it("handles an empty or partial answer", () => {
    expect(normalizeStreamingMarkdown("")).toBe("");
    expect(normalizeStreamingMarkdown("Hel")).toBe("Hel");
  });
});
