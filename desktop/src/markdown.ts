/**
 * Streaming-safe markdown.
 *
 * A stored transcript is complete markdown; a streamed answer is not -- it arrives
 * token by token, so the last line can be half a syntax marker and a code block can
 * be missing its closing fence. The parser is already tolerant of everything else
 * (partial emphasis, a list that is still growing, a table row without its
 * separator), so these helpers only repair what actually renders wrong mid-stream:
 *
 *  * a fence marker that is still being typed (` ` ` `` `) is dropped instead of
 *    showing up as literal backticks at the end of the answer;
 *  * a fence that was opened and not closed is closed, so the block renders as a
 *    code block with the right language instead of swallowing the following text.
 *
 * Deliberately pure and dependency-free: the desktop shell has no DOM test harness,
 * so the streaming rules are unit-tested directly (see `markdown.test.ts`).
 */

/** A fence marker: three or more backticks/tildes, indented at most three spaces. */
const FENCE = /^ {0,3}(`{3,}|~{3,})(.*)$/;
/** A fence marker still being typed: one or two of them, alone on the last line. */
const PARTIAL_FENCE = /^ {0,3}(`{1,2}|~{1,2})$/;

export type FenceState = {
  /** The fence character of the block that never closed, or null when balanced. */
  open: null | "`" | "~";
  /** True when the last line is a fence marker that is still being typed. */
  partial: boolean;
};

/** Which fence (if any) a document leaves open. Used by the renderer and the tests. */
export function fenceState(text: string): FenceState {
  const lines = text.split("\n");
  const last = lines[lines.length - 1];
  const partial = PARTIAL_FENCE.test(last);
  const body = partial ? lines.slice(0, -1) : lines;
  let open: string | null = null;
  for (const line of body) {
    const match = FENCE.exec(line);
    if (!match) continue;
    const marker = match[1];
    if (open === null) {
      open = marker;
    } else if (marker[0] === open[0] && marker.length >= open.length && match[2].trim() === "") {
      // A closing fence: same character, at least as long, nothing after it.
      open = null;
    }
  }
  return { open: open === null ? null : (open[0] as "`" | "~"), partial };
}

/**
 * The text to hand to the parser for a message that may still be arriving.
 * Complete markdown is returned unchanged, so this is safe on stored transcripts too.
 */
export function normalizeStreamingMarkdown(text: string): string {
  if (!text) return "";
  const { open, partial } = fenceState(text);
  let body = text;
  if (partial) {
    const lines = body.split("\n");
    lines.pop();
    body = lines.join("\n");
  }
  return open ? `${body}\n${open.repeat(3)}` : body;
}
