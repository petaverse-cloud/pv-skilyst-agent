import { describe, expect, it } from "vitest";
import { extractCanvasFocus } from "./WorkbenchPage";

// locate()'s data path, exercised with real-shaped action rows (the runtime
// writes "params" per src/agent/tools.py; board_delta/result_ref verbatim).
// This test exists because the M2 rewrite silently read a non-existent field
// and every layout-level mock missed it (review finding #1 on PR #41).

describe("extractCanvasFocus (locate data path)", () => {
  it("focuses the workflow from the action's params", () => {
    const focus = extractCanvasFocus({
      role: "action",
      content: "",
      params: { workflow_id: "workflow-1" },
    });
    expect(focus).toEqual({ workflow_id: "workflow-1", node_key: undefined });
  });

  it("prefers result_ref.node for the node key", () => {
    const focus = extractCanvasFocus({
      role: "action",
      content: "",
      params: { workflow_id: "workflow-1" },
      result_ref: { node: "script-2" },
    });
    expect(focus).toEqual({ workflow_id: "workflow-1", node_key: "script-2" });
  });

  it("falls back to the first wired node in result_ref", () => {
    const focus = extractCanvasFocus({
      role: "action",
      content: "",
      params: { workflow_id: "workflow-1" },
      result_ref: { wired: ["mat-1", "minimax-1"] },
    });
    expect(focus).toEqual({ workflow_id: "workflow-1", node_key: "mat-1" });
  });

  it("falls back to board_delta.added_node", () => {
    const focus = extractCanvasFocus({
      role: "action",
      content: "",
      params: { workflow_id: "workflow-1" },
      board_delta: { added_node: "image-3" },
    });
    expect(focus).toEqual({ workflow_id: "workflow-1", node_key: "image-3" });
  });

  it("returns null when the action carries no workflow_id", () => {
    expect(extractCanvasFocus({ role: "action", content: "" })).toBeNull();
  });
});
