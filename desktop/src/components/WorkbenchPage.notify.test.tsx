// @vitest-environment happy-dom
// #82: the SEAM test — WorkbenchPage.send must actually CALL notifyRunEnd
// when a run finishes (the #87 r2 lesson, applied before review this time:
// the module's own tests pin the logic; this pins the wiring. Deleting the
// call site must fail HERE, not survive to review).
import { describe, expect, it, vi, beforeEach } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import React, { act, createElement } from "react";
import { MantineProvider } from "@mantine/core";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// Mantine Autosize touches document.fonts + ResizeObserver; happy-dom ships neither.
if (typeof globalThis.ResizeObserver === "undefined") {
  class ResizeObserverStub {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  }
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
  getWorkflow: vi.fn().mockResolvedValue({}),
}));
vi.mock("./CanvasView", () => ({
  CanvasView: () => createElement("div", { "data-testid": "canvas" }),
}));

import WorkbenchPage from "./WorkbenchPage";

const R = React;

// Composer is mocked with an onSend CAPTURE: WorkbenchPage hands send() to
// the real Composer; the mock grabs that exact closure so the test drives
// the REAL send path (the production code under test is WorkbenchPage).
let capturedSend: (text: string) => void = () => undefined;
vi.mock("./Composer", () => ({
  default: (props: { onSend: (t: string) => void }) => {
    capturedSend = props.onSend;
    return createElement("div", { "data-testid": "composer" });
  },
  withAssetContext: (text: string) => text,
}));

function mountWorkbench(): Root {
  document.body.innerHTML = "";
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
          sessions: [],
          runtimeConnected: true,
        } as never),
      ),
    );
  });
  return root;
}

describe("WorkbenchPage -> notifyRunEnd wiring (#82)", () => {
  beforeEach(() => {
    notifyMock.mockReset();
    sendMessageMock.mockReset();
  });

  it("a completed run fires the notification with the run's outcome", async () => {
    sendMessageMock.mockResolvedValueOnce({
      ok: true, session_id: "s1", stop_reason: "done", messages: [],
    });
    mountWorkbench();
    await act(async () => {
      capturedSend("make a wide shot of the harbor");
    });
    expect(notifyMock).toHaveBeenCalledTimes(1);
    expect(notifyMock).toHaveBeenCalledWith(
      expect.objectContaining({ ok: true, body: expect.stringContaining("harbor") }),
    );
  });

  it("a failed run fires the failure notification (attention matters more)", async () => {
    sendMessageMock.mockResolvedValueOnce({
      ok: false, session_id: "s2", stop_reason: "BudgetExceeded", error: "refused",
      messages: [],
    });
    mountWorkbench();
    await act(async () => {
      capturedSend("something paid");
    });
    expect(notifyMock).toHaveBeenCalledWith(
      expect.objectContaining({ ok: false, body: expect.stringContaining("BudgetExceeded") }),
    );
  });

  it("a thrown send still notifies (the catch path is wired too)", async () => {
    sendMessageMock.mockRejectedValueOnce(new Error("runtime connection lost"));
    mountWorkbench();
    await act(async () => {
      capturedSend("anything");
    });
    expect(notifyMock).toHaveBeenCalledWith(
      expect.objectContaining({ ok: false, body: expect.stringContaining("connection lost") }),
    );
  });
});
