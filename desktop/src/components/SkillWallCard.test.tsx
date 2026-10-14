// @vitest-environment happy-dom
// #102 (core#833): the wall card contract — one card = one SKILL, preview
// media up front (type by extension), display_name + fork count footer,
// tier badge, fork lands in the workbench. slug NEVER renders (#818).
import { describe, expect, it, vi, beforeEach } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import React, { act, createElement } from "react";
import { MantineProvider } from "@mantine/core";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
if (typeof globalThis.ResizeObserver === "undefined") {
  class ResizeObserverStub { observe(): void {} unobserve(): void {} disconnect(): void {} }
  globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
}

const navigateMock = vi.hoisted(() => vi.fn());
vi.mock("../router", () => ({ navigate: navigateMock }));

import SkillWallCard from "./SkillWallCard";
import type { SkillPreviewCard } from "../beehiveClient";

const base: SkillPreviewCard = {
  skill_id: "skill-1", display_name: "Harbor Sunset", slug: "harbor-sunset",
  tier: "official", fork_count: 7, previews: [], created_at: "2026-10-13T00:00:00Z",
};

function mount(card: SkillPreviewCard): HTMLElement {
  document.body.innerHTML = "";
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root: Root = createRoot(host);
  act(() => {
    root.render(createElement(MantineProvider, { defaultColorScheme: "dark" },
      createElement(SkillWallCard, { card })));
  });
  return host;
}

beforeEach(() => navigateMock.mockReset());

describe("SkillWallCard (#102, core#833 card contract)", () => {
  it("renders display_name, tier badge, and fork count; slug never appears", () => {
    const host = mount(base);
    expect(host.textContent).toContain("Harbor Sunset");
    expect(host.textContent).toContain("official");
    expect(host.textContent).toContain("7 forks");
    expect(host.textContent).not.toContain("harbor-sunset");
  });

  it("a video preview renders an inline player (media-first)", () => {
    const host = mount({ ...base, previews: ["https://cdn/x.mp4"] });
    const video = host.querySelector('[data-testid="skill-preview-video"]');
    expect(video).toBeTruthy();
    expect((video as HTMLVideoElement).src).toBe("https://cdn/x.mp4");
  });

  it("an image preview renders natively; a broken image falls to the typed placeholder", () => {
    const host = mount({ ...base, previews: ["https://cdn/x.png"] });
    expect(host.querySelector('[data-testid="skill-preview-image"]')).toBeTruthy();
    const img = host.querySelector('[data-testid="skill-preview-image"]') as HTMLImageElement;
    act(() => { img.dispatchEvent(new Event("error")); });
    expect(host.textContent).toContain("Preview coming with the demo runs");
  });

  it("no previews renders the typed placeholder, never a gray box", () => {
    const host = mount(base);
    expect(host.querySelector('[data-testid="skill-preview-empty"]')).toBeTruthy();
  });

  it("the fork button routes to the workbench", () => {
    const host = mount(base);
    const btn = (host.querySelector('[data-testid="skill-fork-button"] button')
      ?? host.querySelector('[data-testid="skill-fork-button"]')) as HTMLButtonElement;
    act(() => { btn.click(); });
    expect(navigateMock).toHaveBeenCalledWith({ module: "workbench" });
  });

  it("singular fork count reads 'fork', plural reads 'forks'", () => {
    expect(mount({ ...base, fork_count: 1 }).textContent).toContain("1 fork");
    expect(mount({ ...base, fork_count: 2 }).textContent).toContain("2 forks");
  });
});
