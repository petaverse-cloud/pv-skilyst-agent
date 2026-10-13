// @vitest-environment happy-dom
// #91: the creation card — the web#386 shared contract, pinned at the
// desktop surface. Main content = play target; action row = the four
// affordances; like goes through the runtime proxy; comments is the
// structural placeholder the issue scopes.
import { describe, expect, it, vi, beforeEach } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import React, { act, createElement } from "react";
import { MantineProvider } from "@mantine/core";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const favoriteMock = vi.hoisted(() => vi.fn());
vi.mock("../beehiveClient", async (importOriginal) => {
  const orig = await importOriginal<typeof import("../beehiveClient")>();
  return { ...orig, setCreationFavorite: favoriteMock };
});
// router: navigate must not blow in happy-dom
vi.mock("../router", () => ({ navigate: vi.fn() }));

import { CreationCard } from "./CreationCard";
import type { CreationWork } from "../beehiveClient";

const R = React;

const videoWork: CreationWork = {
  id: "work_1", name: "Harbor at dusk", asset_type: "video",
  mime_type: "video/mp4", url: "https://cdn.example/harbor.mp4",
  thumbnail_url: "https://cdn.example/harbor.jpg", duration: 12.4,
  created_at: "2026-10-13T00:00:00Z",
};
const imageWork: CreationWork = {
  id: "work_2", name: "Still frame", asset_type: "image",
  mime_type: "image/png", url: "https://cdn.example/still.png",
  created_at: "2026-10-13T00:00:00Z",
};

function mount(work: CreationWork): HTMLElement {
  document.body.innerHTML = "";
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root: Root = createRoot(host);
  act(() => {
    root.render(
      createElement(MantineProvider, { defaultColorScheme: "dark" },
        createElement(CreationCard, { work })));
  });
  return host;
}

beforeEach(() => favoriteMock.mockReset().mockResolvedValue(undefined));

describe("CreationCard (#91)", () => {
  it("a video card renders an inline play target with poster and duration", () => {
    const host = mount(videoWork);
    const video = host.querySelector('[data-testid="creation-media-video"]') as HTMLVideoElement;
    expect(video).toBeTruthy();
    expect(video.getAttribute("src")).toContain("harbor.mp4");
    expect(video.getAttribute("poster")).toContain("harbor.jpg");
    const duration = host.querySelector('[data-testid="creation-duration"]');
    expect(duration?.textContent).toContain("12s");
  });

  it("an image card renders the image as its own content", () => {
    const host = mount(imageWork);
    expect(host.querySelector('[data-testid="creation-media-image"]')).toBeTruthy();
    expect(host.querySelector('[data-testid="creation-media-video"]')).toBeNull();
  });

  it("the action row carries all four affordances", () => {
    const host = mount(videoWork);
    expect(host.querySelector('[data-testid="creation-skills-preview"]')).toBeTruthy();
    expect(host.querySelector('[data-testid="creation-fork"]')).toBeTruthy();
    expect(host.querySelector('[data-testid="creation-like"]')).toBeTruthy();
    // comments is the structural placeholder: present, disabled
    const comments = host.querySelector('[data-testid="creation-comments"]') as HTMLButtonElement;
    expect(comments).toBeTruthy();
    expect(comments.hasAttribute("disabled")).toBe(true);
  });

  it("like goes through the favorite contract and reflects the state", async () => {
    const host = mount(videoWork);
    const like = host.querySelector('[data-testid="creation-like"]') as HTMLButtonElement;
    await act(async () => {
      like.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(favoriteMock).toHaveBeenCalledWith("work_1", true);
    // second click un-likes
    await act(async () => {
      like.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(favoriteMock).toHaveBeenLastCalledWith("work_1", false);
  });

  it("a failed like renders the loud in-card error", async () => {
    favoriteMock.mockRejectedValueOnce(new Error("sign in to like (409)"));
    const host = mount(videoWork);
    const like = host.querySelector('[data-testid="creation-like"]') as HTMLButtonElement;
    await act(async () => {
      like.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    const error = host.querySelector('[data-testid="creation-like-error"]');
    expect(error?.textContent).toContain("409");
  });

  it("unsupported media degrades to a typed placeholder, not a gray box", () => {
    const host = mount({ ...imageWork, asset_type: "audio", mime_type: "audio/mpeg" });
    const unknown = host.querySelector('[data-testid="creation-media-unknown"]');
    expect(unknown?.textContent).toContain("audio/mpeg");
  });
});
