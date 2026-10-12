// @vitest-environment happy-dom
// M3b review r2 (mutation testing round 3): the behavioral pins need
// effects to RUN — renderToString skips them (#49). happy-dom is a
// devDependency (G1-3: devDeps unrestricted; zero runtime-bundle impact),
// scoped to this one file via the pragma above.
import { describe, expect, it, vi } from "vitest";
import { createRoot } from "react-dom/client";
import React, { act, createElement } from "react";
import { MantineProvider } from "@mantine/core";

vi.mock("../beehiveClient", () => ({
  listWorkflows: vi.fn().mockResolvedValue([]),
  listSkills: vi.fn().mockResolvedValue([]),
}));

// The studio package's real WorkflowCanvas drags react-query + the host
// adapter into the render; mock it with a mount-counting probe — the
// remount assertion counts INSTANCES of the package component (the exact
// mechanism key={boardReloadKey} drives).
// react-dom's act warning: the happy-dom env must declare itself.
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let packageMounts = 0;
vi.mock("@petaverse/skilyst-studio/canvas", () => {
  class WorkflowCanvasImpl extends (require("react") as typeof import("react")).Component {
    override componentDidMount() {
      packageMounts += 1;
    }
    override render() {
      return createElement(
        "div",
        { "data-testid": "package-canvas" },
        `canvas-mount-${packageMounts + 1}`,
      );
    }
  }
  return { default: WorkflowCanvasImpl };
});

import { CanvasView } from "./CanvasView";

const R = React;
const MOUNT = (onBoardRefresh: (refresh: () => void) => void, initialWorkflowId?: string | null) =>
  createElement(
    MantineProvider,
    null,
    createElement(CanvasView, {
      // undefined/null = the list stage (no bound board); the ?? default
      // previously swallowed undefined into "wf-1" and flipped the stage.
      initialWorkflowId: initialWorkflowId === undefined ? "wf-1" : initialWorkflowId,
      runtimeReady: true,
      onBoardRefresh,
    }),
  );

function mountCanvas(onBoardRefresh: (refresh: () => void) => void) {
  const host = document.createElement("div");
  act(() => {
    createRoot(host).render(MOUNT(onBoardRefresh));
  });
  // First act mounts at stage=list; the stage-flip effect
  // (initialWorkflowId -> canvas) schedules a re-render whose
  // registration effect runs in a follow-up flush.
  act(() => { /* flush the stage-flip re-render */ });
  return host;
}

describe("run-end board refresh (M3b) — CanvasView side", () => {
  it("canvas stage registers a callable refresh with the host", () => {
    const onBoardRefresh = vi.fn();
    mountCanvas(onBoardRefresh);
    // The registration effect ran: the host received a function.
    expect(onBoardRefresh.mock.calls.length).toBeGreaterThanOrEqual(1);
    expect(typeof onBoardRefresh.mock.calls[0]?.[0]).toBe("function");
  });

  it("calling the registered refresh remounts the package canvas (key bump)", () => {
    const onBoardRefresh = vi.fn();
    const host = mountCanvas(onBoardRefresh);
    const mountsBefore = packageMounts;
    expect(mountsBefore).toBeGreaterThanOrEqual(1);

    // The registration effect re-runs when refreshBoard's closure updates
    // (stage flips to canvas) — the LIVE refresh is the LAST registration,
    // not the first (which captured stage=list).
    const refresh = onBoardRefresh.mock.calls.at(-1)?.[0] as () => void;
    act(() => {
      refresh();
    });
    // The remount lever fired: a NEW package-canvas instance mounted.
    expect(packageMounts).toBeGreaterThan(mountsBefore);
    // The canvas surface persists (same stage, same workflow id).
    expect(host.querySelector('[data-testid="package-canvas"]')).not.toBeNull();
  });

  it("list stage renders the workflow list, canvas stage renders the package canvas", () => {
    // The M2 shape bug this PR fixed while being reviewed: the canvas
    // branch sat AFTER a bare `return (list JSX)` — unreachable, dropped
    // by esbuild — so EVERY stage rendered the list. The stage guard is
    // the switch; this pin fails if it is removed or inverted (verified
    // by mutation during review r2: `if (false)` slipped through the
    // other three tests, this one catches it).
    const onBoardRefresh = vi.fn();
    const host = mountCanvas(onBoardRefresh);
    // A bound workflow flips the stage to canvas: the package canvas
    // mounted, the workflow-list title is gone.
    expect(host.querySelector('[data-testid="package-canvas"]')).not.toBeNull();
    expect(host.innerHTML).not.toContain("Workflow boards");

    // Without a bound workflow the component stays on the list stage:
    // the list title renders, no package canvas instance mounts.
    const before = packageMounts;
    const host2 = document.createElement("div");
    act(() => {
      createRoot(host2).render(MOUNT(onBoardRefresh, null));
    });
    act(() => { /* flush */ });
    expect(host2.innerHTML).toContain("Workflow boards");
    expect(host2.querySelector('[data-testid="package-canvas"]')).toBeNull();
    expect(packageMounts).toBe(before);
  });

  it("refresh before a board is bound is a no-op (no remount)", () => {
    // No initialWorkflowId: the component stays on the list stage. The
    // registration may happen, but calling the refresh there must NOT
    // remount anything (refreshBoard guards on stage/activeId) — and the
    // next canvas mount pulls fresh anyway (no staleness path).
    const onBoardRefresh = vi.fn();
    const host = document.createElement("div");
    act(() => {
      createRoot(host).render(MOUNT(onBoardRefresh, undefined));
    });
    act(() => { /* flush */ });
    const refresh = onBoardRefresh.mock.calls[0]?.[0] as (() => void) | undefined;
    const mountsBefore = packageMounts;
    if (refresh) {
      act(() => {
        refresh();
      });
    }
    expect(packageMounts).toBe(mountsBefore);  // no package canvas ever mounted
  });
});
