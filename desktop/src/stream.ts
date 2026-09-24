/**
 * The state of one in-flight run, as the conversation view needs to see it.
 *
 * The runtime's `delta` events are model text and its `note` events are progress
 * lines from the loop (a tool call starting, a job being polled). A run can emit
 * text, then a tool call, then more text -- so the streamed text is kept as finished
 * blocks plus the block that is still growing. Concatenating everything into one
 * buffer (what the shell did before) glued separate turns together and lost the
 * boundary where a tool call happened.
 *
 * Pure and unit-tested: this is the part of streaming that has real edge cases.
 */

export type StreamState = {
  /** Turns that finished streaming (a tool call or the end of the run followed). */
  blocks: string[];
  /** The turn currently arriving. */
  current: string;
};

export const emptyStream: StreamState = { blocks: [], current: "" };

/** Model text arrived. */
export function appendDelta(state: StreamState, chunk: string): StreamState {
  if (!chunk) return state;
  return { ...state, current: state.current + chunk };
}

/**
 * A progress note arrived: whatever has streamed so far belongs to the turn that is
 * now over. Whitespace-only buffers are dropped so a job-poll note cannot push an
 * empty bubble into the view.
 */
export function freezeTurn(state: StreamState): StreamState {
  if (!state.current.trim()) return state;
  return { blocks: [...state.blocks, state.current], current: "" };
}

export function isStreaming(state: StreamState): boolean {
  return state.blocks.length > 0 || state.current !== "";
}

/** One line describing what the run is doing right now. */
export function describeActivity(state: StreamState, notes: string[]): string {
  if (state.current) return "writing the answer";
  const last = notes.length ? notes[notes.length - 1].trim() : "";
  if (!last) return "thinking";
  const tool = /^tool\s+(\S+)([\s\S]*)$/.exec(last);
  if (tool) return /refused:/.test(tool[2]) ? `${tool[1]} refused` : `running ${tool[1]}`;
  const job = /^job\s+\S+:\s*(\S+)/.exec(last);
  if (job) return `job ${job[1]}`;
  if (/^integrity\[/.test(last)) return "checking installed skills";
  return last.length > 60 ? `${last.slice(0, 60)}…` : last;
}

/** Elapsed time as the status line shows it. */
export function formatElapsed(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  return `${minutes}m ${String(total % 60).padStart(2, "0")}s`;
}
