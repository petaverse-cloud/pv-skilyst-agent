// @vitest-environment happy-dom
// #84: artifact export — the ConversationView half. The seam under test:
// the Download affordance calls the shell command with the URL and a sane
// name, failures render loudly, the browser fallback refuses honestly.
// (The Rust half — save panel + streaming write — is probed on the built
// bundle; the https-only refusal is a unit of the command itself.)
import { describe, expect, it, vi, beforeEach } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import React, { act, createElement } from "react";
import { MantineProvider } from "@mantine/core";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
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

const invokeMock = vi.hoisted(() => vi.fn());
vi.mock("@tauri-apps/api/core", () => ({ invoke: invokeMock }));
const shellMock = vi.hoisted(() => ({ inShell: vi.fn() }));
vi.mock("../api", () => ({ ...shellMock, api: vi.fn() }));

import ConversationView from "./ConversationView";

const R = React;

function mount(detail: unknown) {
  document.body.innerHTML = "";
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root: Root = createRoot(host);
  act(() => {
    root.render(
      createElement(
        MantineProvider,
        { defaultColorScheme: "dark" },
        createElement(ConversationView, {
          detail: detail as never,
          stream: { blocks: [], current: "" },
          notes: [],
          busy: false,
        } as never),
      ),
    );
  });
  return host;
}

const detailWithArtifact = {
  session_id: "s",
  meta: { title: "t" },
  messages: [],
  artifacts: [
    { artifact_url: "https://cdn.example/video/out.mp4", job_id: "job_1", ts: 1 },
    { artifact_url: "https://cdn.example/img/frame.png", job_id: "job_2", ts: 2 },
  ],
  trace: [],
};

beforeEach(() => {
  invokeMock.mockReset();
  shellMock.inShell.mockReset().mockReturnValue(true);
});

describe("ConversationView artifact export (#84)", () => {
  it("renders a download affordance per artifact row", () => {
    const host = mount(detailWithArtifact);
    const buttons = host.querySelectorAll('[data-testid="download-artifact"]');
    expect(buttons.length).toBe(2);
    // the rows keep their open-in-browser anchors too
    expect(host.querySelectorAll('a[href^="https://cdn.example"]').length).toBe(2);
  });

  it("clicking download invokes save_artifact with the url and the basename", async () => {
    invokeMock.mockResolvedValueOnce("/Users/me/Downloads/out.mp4");
    const host = mount(detailWithArtifact);
    const button = host.querySelectorAll('[data-testid="download-artifact"]')[0];
    await act(async () => {
      button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(invokeMock).toHaveBeenCalledWith("save_artifact", {
      url: "https://cdn.example/video/out.mp4",
      suggestedName: "out.mp4",
    });
    const saved = host.querySelector('[data-testid="exported-path"]');
    expect(saved).toBeTruthy();
    expect(saved!.textContent).toContain("/Users/me/Downloads/out.mp4");
  });

  it("a failed export renders the loud red error row", async () => {
    invokeMock.mockRejectedValueOnce(new Error("artifact download failed: HTTP 404"));
    const host = mount(detailWithArtifact);
    const button = host.querySelectorAll('[data-testid="download-artifact"]')[0];
    await act(async () => {
      button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    const error = host.querySelector('[data-testid="export-error"]');
    expect(error).toBeTruthy();
    expect(error!.textContent).toContain("HTTP 404");
    expect(host.querySelector('[data-testid="exported-path"]')).toBeNull();
  });

  it("the browser dev fallback refuses loudly instead of crashing the invoke", async () => {
    shellMock.inShell.mockReturnValue(false);
    const host = mount(detailWithArtifact);
    const button = host.querySelectorAll('[data-testid="download-artifact"]')[0];
    await act(async () => {
      button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(invokeMock).not.toHaveBeenCalled();
    const error = host.querySelector('[data-testid="export-error"]');
    expect(error).toBeTruthy();
    expect(error!.textContent).toContain("desktop app");
  });

  it("an artifact with no url is refused loudly (nothing to download)", async () => {
    const host = mount({
      ...detailWithArtifact,
      artifacts: [{ job_id: "job_x" }],
    });
    const button = host.querySelector('[data-testid="download-artifact"]');
    await act(async () => {
      button!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(invokeMock).not.toHaveBeenCalled();
    const error = host.querySelector('[data-testid="export-error"]');
    expect(error).toBeTruthy();
    expect(error!.textContent).toContain("no url");
  });
});
