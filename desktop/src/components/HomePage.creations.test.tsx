// @vitest-environment happy-dom
// #91: the Home seam — the Creations feed is Home's FIRST-LEVEL section
// and its data comes through listPublicWorks (the runtime proxy route).
// Deleting the feed wiring must fail here, not survive to review.
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

const listPublicWorksMock = vi.hoisted(() => vi.fn());
vi.mock("../beehiveClient", async (importOriginal) => {
  const orig = await importOriginal<typeof import("../beehiveClient")>();
  return {
    ...orig,
    listPublicWorks: listPublicWorksMock,
    listWorkflows: vi.fn().mockResolvedValue([]),
    listSkills: vi.fn().mockRejectedValue(new Error("missing scope skills:read")),
  };
});
vi.mock("../router", () => ({ navigate: vi.fn() }));

import HomePage from "./HomePage";
import type { CreationWork } from "../beehiveClient";

const R = React;

function mountHome(): HTMLElement {
  document.body.innerHTML = "";
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root: Root = createRoot(host);
  act(() => {
    root.render(
      createElement(MantineProvider, { defaultColorScheme: "dark" },
        createElement(HomePage, {
          sessions: [],
          registry: {},
          runtimeConnected: true,
        })));
  });
  return host;
}

beforeEach(() => listPublicWorksMock.mockReset());

describe("Home Creations feed (#91 seam)", () => {
  it("the feed loads through listPublicWorks on mount", async () => {
    listPublicWorksMock.mockResolvedValueOnce([]);
    await act(async () => { mountHome(); });
    expect(listPublicWorksMock).toHaveBeenCalledWith(24, 0);
  });

  it("creations render as cards in the feed section", async () => {
    const work: CreationWork = {
      id: "w1", name: "Dawn run", asset_type: "video", mime_type: "video/mp4",
      url: "https://cdn/x.mp4", created_at: "2026-10-13T00:00:00Z",
    };
    listPublicWorksMock.mockResolvedValueOnce([work]);
    const host = mountHome();
    await act(async () => {});
    expect(host.querySelector('[data-testid="creation-card"]')).toBeTruthy();
  });

  it("a failed feed renders the loud alert, not a silent gap", async () => {
    listPublicWorksMock.mockRejectedValueOnce(new Error("GET /public/works failed: HTTP 503"));
    const host = mountHome();
    await act(async () => {});
    expect(host.textContent).toContain("Could not load the creations feed");
    expect(host.textContent).toContain("503");
  });
});
