// @vitest-environment happy-dom
// #102: the Home seam — the skill-preview wall is Home's primary feed
// (listSkillPreviews through the runtime proxy), rendered in the masonry
// flow. Deleting the wiring must fail here.
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

const previewsMock = vi.hoisted(() => vi.fn());
vi.mock("../beehiveClient", async (importOriginal) => {
  const orig = await importOriginal<typeof import("../beehiveClient")>();
  return {
    ...orig,
    listSkillPreviews: previewsMock,
    listPublicWorks: vi.fn().mockResolvedValue([]),
    listWorkflows: vi.fn().mockResolvedValue([]),
    listSkills: vi.fn().mockRejectedValue(new Error("missing scope skills:read")),
  };
});
vi.mock("../router", () => ({ navigate: vi.fn() }));

import HomePage from "./HomePage";

function mountHome(): HTMLElement {
  document.body.innerHTML = "";
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root: Root = createRoot(host);
  act(() => {
    root.render(
      createElement(MantineProvider, { defaultColorScheme: "dark" },
        createElement(HomePage, { sessions: [], registry: {}, runtimeConnected: true })));
  });
  return host;
}

beforeEach(() => previewsMock.mockReset());

describe("Home skill wall (#102 seam)", () => {
  it("loads the skill-preview feed on mount", async () => {
    previewsMock.mockResolvedValueOnce({ cards: [], total: 0 });
    await act(async () => { mountHome(); });
    expect(previewsMock).toHaveBeenCalledWith(24, 0);
  });

  it("renders skill cards in the masonry flow (multi-column, break-inside)", async () => {
    previewsMock.mockResolvedValueOnce({
      cards: [{
        skill_id: "s1", display_name: "Dawn", slug: "dawn", tier: "official",
        fork_count: 0, previews: ["https://cdn/x.mp4"], created_at: "2026-10-13T00:00:00Z",
      }],
      total: 1,
    });
    const host = mountHome();
    await act(async () => {});
    const masonry = host.querySelector(".skill-wall-masonry") as HTMLElement | null;
    expect(masonry).toBeTruthy();
    expect(masonry!.style.columns.startsWith("3")).toBe(true);
    const cell = masonry!.firstElementChild as HTMLElement | null;
    expect(cell).toBeTruthy();
    expect(cell!.style.breakInside).toBe("avoid");
    expect(host.querySelector('[data-testid="skill-wall-card"]')).toBeTruthy();
  });

  it("a failed wall is a loud alert, never a silent gap", async () => {
    previewsMock.mockRejectedValueOnce(new Error("GET /public/skill-previews failed: HTTP 503"));
    const host = mountHome();
    await act(async () => {});
    expect(host.textContent).toContain("Could not load the skill wall");
    expect(host.textContent).toContain("503");
  });

  it("an empty wall explains itself", async () => {
    previewsMock.mockResolvedValueOnce({ cards: [], total: 0 });
    const host = mountHome();
    await act(async () => {});
    expect(host.textContent).toContain("No published skills on the wall yet");
  });
});
