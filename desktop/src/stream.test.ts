import { describe, expect, it } from "vitest";
import {
  appendDelta,
  describeActivity,
  emptyStream,
  formatElapsed,
  freezeTurn,
  isStreaming,
} from "./stream";

describe("stream buffers", () => {
  it("accumulates deltas into the current block", () => {
    const state = appendDelta(appendDelta(emptyStream, "Hel"), "lo");
    expect(state).toEqual({ blocks: [], current: "Hello" });
    expect(isStreaming(state)).toBe(true);
  });

  it("ignores an empty delta", () => {
    expect(appendDelta(emptyStream, "")).toBe(emptyStream);
  });

  it("freezes a turn when a note arrives, so turns do not glue together", () => {
    const first = freezeTurn(appendDelta(emptyStream, "I will read the skill."));
    expect(first).toEqual({ blocks: ["I will read the skill."], current: "" });
    const second = freezeTurn(appendDelta(first, "Done."));
    expect(second.blocks).toEqual(["I will read the skill.", "Done."]);
    expect(second.current).toBe("");
  });

  it("does not push an empty block for a job-progress note", () => {
    const state = { blocks: ["first"], current: "   " };
    expect(freezeTurn(state)).toBe(state);
  });

  it("reports nothing streaming on a fresh state", () => {
    expect(isStreaming(emptyStream)).toBe(false);
  });
});

describe("describeActivity", () => {
  it("says thinking before anything has arrived", () => {
    expect(describeActivity(emptyStream, [])).toBe("thinking");
  });

  it("says the model is writing while text streams", () => {
    expect(describeActivity(appendDelta(emptyStream, "Sure"), [])).toBe("writing the answer");
  });

  it("names the tool that is running", () => {
    const notes = ['tool read_skill {"skill_id": "skilyst/video-15s"}'];
    expect(describeActivity(emptyStream, notes)).toBe("running read_skill");
  });

  it("says a refused tool call was refused", () => {
    const notes = ["tool beehive_submit_job refused: node is not runnable"];
    expect(describeActivity(emptyStream, notes)).toBe("beehive_submit_job refused");
  });

  it("shows the job state while a paid job is polled", () => {
    expect(describeActivity(emptyStream, ["job job-1790: running progress=0.4"])).toBe("job running");
  });

  it("describes integrity and unknown notes", () => {
    expect(describeActivity(emptyStream, ["integrity[skilyst/doctor]: digest mismatch"])).toBe(
      "checking installed skills",
    );
    expect(describeActivity(emptyStream, ["something new"])).toBe("something new");
  });

  it("truncates a long unknown note", () => {
    const long = "x".repeat(200);
    expect(describeActivity(emptyStream, [long]).length).toBe(61);
  });
});

describe("formatElapsed", () => {
  it("renders seconds and minutes", () => {
    expect(formatElapsed(0)).toBe("0s");
    expect(formatElapsed(9.7)).toBe("9s");
    expect(formatElapsed(59)).toBe("59s");
    expect(formatElapsed(65)).toBe("1m 05s");
    expect(formatElapsed(-3)).toBe("0s");
  });
});
