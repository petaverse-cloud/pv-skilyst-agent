// @vitest-environment happy-dom
// #83: the skilyst://workflow deep-link seam. The shell parses
// skilyst://workflow?edit=<id> (Rust unit tests pin the parser: happy
// path, wrong host, malformed ids). This file pins the WEBVIEW half:
// the open-workflow event routes to the workbench with the workflow
// bound — the exact navigation a console "open in app" click depends on.
import { describe, expect, it, vi, beforeEach } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import React, { act, createElement } from "react";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// Environment hardening: in some vitest+happy-dom ESM combinations
// window.localStorage is undefined at first mount (the app reads the
// model preference there). A memory-backed polyfill removes the
// environment from the test's outcome — the test pins the deep-link
// contract, not the DOM shim's mood.
const domWindow = window as unknown as { localStorage?: Storage };
if (!domWindow.localStorage) {
  const store = new Map<string, string>();
  const shim: Storage = {
    getItem: (k: string): string | null => (store.get(k) ?? null),
    setItem: (k: string, v: string) => void store.set(k, String(v)),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
    key: (i: number) => Array.from(store.keys())[i] ?? null,
    get length() { return store.size; },
  };
  domWindow.localStorage = shim;
}

// The App module's real collaborators: api (runtime not connected in
// tests), the tauri event listen (captured so the test can FIRE the
// shell event), and the router's navigate (asserted).
const listenMock = vi.hoisted(() => vi.fn());
vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));
vi.mock("@tauri-apps/api/event", () => ({ listen: listenMock }));
vi.mock("./api", () => ({
  api: vi.fn(),
  inShell: vi.fn().mockReturnValue(true),
  sendMessage: vi.fn(),
  resolveConfirm: vi.fn(),
  postAuthLogin: vi.fn(),
  postAuthLogout: vi.fn(),
  postAuthRefresh: vi.fn(),
  fetchAuthStatus: vi.fn().mockResolvedValue({ state: "authenticated", authenticated: true }),
}));

import App from "./App";
import { MantineProvider } from "@mantine/core";
import * as router from "./router";
const navigateSpy = vi.spyOn(router, "navigate");

function mountApp(): Root {
  document.body.innerHTML = "";
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  act(() => {
    root.render(
      createElement(MantineProvider, { defaultColorScheme: "dark" }, createElement(App)),
    );
  });
  return root;
}

beforeEach(() => {
  listenMock.mockReset().mockResolvedValue(() => undefined);
  window.location.hash = "";
});

describe("open-workflow deep-link routing (#83)", () => {
  it("the app subscribes to the shell's open-workflow event in the shell", () => {
    mountApp();
    expect(listenMock).toHaveBeenCalledWith("open-workflow", expect.any(Function));
  });

  it("a fired event navigates to the workbench with the workflow bound", () => {
    mountApp();
    const call = listenMock.mock.calls.find((c) => c[0] === "open-workflow");
    const handler = call?.[1] as (event: { payload: { id: string } }) => void;
    expect(handler).toBeTruthy();
    act(() => {
      handler({ payload: { id: "wf_deep_link_1" } });
    });
    expect(navigateSpy).toHaveBeenCalledWith({
      module: "workbench",
      workflow: "wf_deep_link_1",
    });
    // the hash actually moved — the route the WorkbenchPage will read
    expect(window.location.hash).toContain("workflow=wf_deep_link_1");
  });
});
