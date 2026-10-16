// @vitest-environment happy-dom
// #103: the SEAM pins for conversational board editing —
// 1. a turn on an active board carries workflow_id to the runtime (the
//    agent speaks board-aware);
// 2. a streamed canvas action (onBoardAction) refreshes the canvas DURING
//    the run (per-action, not post-hoc);
// 3. a boardless turn carries no binding (today's behavior).
import { describe, expect, it, vi, beforeEach } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import React, { act, createElement } from "react";
import { MantineProvider } from "@mantine/core";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
if (typeof globalThis.ResizeObserver === "undefined") {
  class ResizeObserverStub { observe(): void {} unobserve(): void {} disconnect(): void {} }
  globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
}
const doc = document as unknown as { fonts?: unknown };
if (!doc.fonts) doc.fonts = new EventTarget();

const notifyMock = vi.hoisted(() => vi.fn());
vi.mock("../notifications", () => ({ notifyRunEnd: notifyMock, clearUnseenRuns: vi.fn() }));
const sendMessageMock = vi.hoisted(() => vi.fn());
const apiMock = vi.hoisted(() => vi.fn());
vi.mock("../api", async (importOriginal) => {
  const orig = await importOriginal<typeof import("../api")>();
  return {
    ...orig,
    sendMessage: sendMessageMock,
    api: apiMock.mockResolvedValue({
      session_id: "s", title: "", created_at: "", updated_at: "",
      messages: [], artifacts: [],
    }),
  };
});
vi.mock("../beehiveClient", () => ({
  getWalletBalance: vi.fn().mockResolvedValue(null),
  getWorkflow: vi.fn().mockResolvedValue({ id: "wf-1", name: "Board one", nodes: [] }),
}));

// CanvasView mock CAPTURES the refreshBoard lever (the M3b remount hook
// exposed via props) — firing it must be observable.
let refreshCalls = 0;
vi.mock("./CanvasView", () => ({
  CanvasView: (props: { onBoardRefresh?: (fn: () => void) => void }) => {
    props.onBoardRefresh?.(() => { refreshCalls++; });
    return createElement("div", { "data-testid": "canvas" });
  },
}));

import WorkbenchPage from "./WorkbenchPage";

let capturedSend: (text: string) => void = () => undefined;
vi.mock("./Composer", () => ({
  default: (props: { onSend: (t: string) => void }) => {
    capturedSend = props.onSend;
    return createElement("div", { "data-testid": "composer" });
  },
  withAssetContext: (text: string) => text,
}));

const R = React;

function mountWorkbench(workflowId?: string): Root {
  document.body.innerHTML = "";
  refreshCalls = 0;
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  act(() => {
    root.render(
      createElement(
        MantineProvider,
        { defaultColorScheme: "dark" },
        createElement(WorkbenchPage, {
          info: { dry_run: true, port: 12345, pid: 1 },
          model: "",
          onModelChange: () => undefined,
          dryRun: true,
          onDryRunChange: () => undefined,
          onRegistryChange: () => undefined,
          registry: {},
          sessions: [],
          runtimeConnected: true,
          routeWorkflowId: workflowId,
        } as never),
      ),
    );
  });
  return root;
}

describe("WorkbenchPage board-editing seam (#103)", () => {
  beforeEach(() => {
    sendMessageMock.mockReset();
    refreshCalls = 0;
  });

  it("a turn on the active board binds workflow_id into the message", async () => {
    sendMessageMock.mockResolvedValueOnce({
      ok: true, session_id: "s1", stop_reason: "done", messages: [],
    });
    mountWorkbench("wf-1");
    await act(async () => {
      capturedSend("make it warmer");
    });
    expect(sendMessageMock).toHaveBeenCalledWith(
      expect.objectContaining({ workflow_id: "wf-1" }),
      expect.anything(),
    );
  });

  it("a streamed canvas action refreshes the board DURING the run", async () => {
    // >=1 alone is satisfiable by the run-END refresh alone (M3b) — the pin
    // must catch the action-time refresh specifically.
    let midRunRefreshes = 0;
    sendMessageMock.mockImplementationOnce(async (_req, handlers) => {
      const before = refreshCalls;
      handlers.onBoardAction?.({ tool: "canvas_create_node", workflow_id: "wf-1",
                                board_delta: { added: ["n1"] } });
      midRunRefreshes = refreshCalls - before;
      return { ok: true, session_id: "s1", stop_reason: "done", messages: [] };
    });
    mountWorkbench("wf-1");
    await act(async () => {
      capturedSend("add a node");
    });
    expect(midRunRefreshes).toBeGreaterThanOrEqual(1);
  });

  it("an action for a DIFFERENT board does not refresh the active canvas MID-RUN", async () => {
    // The run-END refresh (M3b's post-hoc lever) always fires; what must NOT
    // happen is the mid-run refresh for a foreign board. Snapshot the count
    // INSIDE the mock, before the run can end.
    let midRunRefreshes = 0;
    sendMessageMock.mockImplementationOnce(async (_req, handlers) => {
      const before = refreshCalls;
      handlers.onBoardAction?.({ tool: "canvas_create_node", workflow_id: "wf-OTHER",
                                board_delta: { added: ["n1"] } });
      midRunRefreshes = refreshCalls - before;
      return { ok: true, session_id: "s1", stop_reason: "done", messages: [] };
    });
    mountWorkbench("wf-1");
    await act(async () => {
      capturedSend("add a node");
    });
    expect(midRunRefreshes).toBe(0);
  });

  it("a boardless turn sends no binding (today's behavior preserved)", async () => {
    sendMessageMock.mockResolvedValueOnce({
      ok: true, session_id: "s1", stop_reason: "done", messages: [],
    });
    mountWorkbench(undefined);
    await act(async () => {
      capturedSend("hello");
    });
    const req = sendMessageMock.mock.calls[0][0];
    expect(req.workflow_id).toBeUndefined();
  });
});
