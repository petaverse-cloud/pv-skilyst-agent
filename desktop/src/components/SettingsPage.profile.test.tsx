// @vitest-environment happy-dom
// #99 profile face: password change + OAuth connections. The pins are the
// user-visible contract — the form gates on min-length, the change result
// lands as a visible message (success AND failure), unbind refreshes the
// list, and the empty state points at the web console (bind stays there).
import { describe, expect, it, vi, beforeEach } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import React, { act, createElement } from "react";
import { MantineProvider } from "@mantine/core";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
if (typeof globalThis.ResizeObserver === "undefined") {
  class ResizeObserverStub { observe(): void {} unobserve(): void {} disconnect(): void {} }
  globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
}

const changePwMock = vi.hoisted(() => vi.fn());
const connectionsMock = vi.hoisted(() => vi.fn());
const unbindMock = vi.hoisted(() => vi.fn());
vi.mock("../api", () => ({ api: vi.fn().mockResolvedValue(null) }));
vi.mock("../beehiveClient", () => ({
  getBillingHistory: vi.fn().mockResolvedValue({ events: [], total: 0 }),
  getWalletBalance: vi.fn().mockResolvedValue(null),
  changePassword: changePwMock,
  getOAuthConnections: connectionsMock,
  unbindOAuth: unbindMock,
}));

import SettingsPage from "./SettingsPage";
import type { AuthStatus } from "./LoginScreen";

const auth: AuthStatus = {
  state: "authenticated",
  authenticated: true,
  account: { uid: "u1", name: "Wesley" },
  scopes: ["workflows:read"],
  expires_in: 86400,
};

function mount(): HTMLElement {
  document.body.innerHTML = "";
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root: Root = createRoot(host);
  act(() => {
    root.render(
      createElement(MantineProvider, { defaultColorScheme: "dark" },
        createElement(SettingsPage, {
          info: { dry_run: true, port: 8765, pid: 1 } as never,
          model: "",
          onModelChange: () => undefined,
          onStopRuntime: () => undefined,
          onStartRuntime: () => undefined,
          connecting: false,
          authStatus: auth,
          onSignOut: () => undefined,
          onRefreshCredential: () => undefined,
        })));
  });
  return host;
}

const R = React;

beforeEach(() => {
  changePwMock.mockReset();
  connectionsMock.mockReset();
  unbindMock.mockReset();
});

/** React controlled inputs ignore a plain value= assignment (the tracker
 *  does not see it) — set through the NATIVE setter so the input event
 *  actually changes what React reads back. */
function setType(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype, "value",
  )!.set!;
  setter.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

describe("SettingsPage profile face (#99)", () => {
  it("renders bound OAuth connections with an unbind action per row", async () => {
    connectionsMock.mockResolvedValueOnce([
      { provider: "google", email: "w@example.com", created_at: "2026-10-01T00:00:00Z" },
      { provider: "github", email: "w@example.com" },
    ]);
    const host = mount();
    await act(async () => {});
    expect(host.textContent).toContain("google");
    expect(host.textContent).toContain("w@example.com");
    expect(host.querySelector('[data-testid="unbind-github"]')).toBeTruthy();
  });

  it("an empty connections list points at the web console for binding", async () => {
    connectionsMock.mockResolvedValueOnce([]);
    const host = mount();
    await act(async () => {});
    expect(host.textContent).toContain("No OAuth providers bound");
    expect(host.textContent).toContain("web console");
  });

  it("a failed connections fetch degrades to the empty state, never a crash", async () => {
    connectionsMock.mockRejectedValueOnce(new Error("503 OAuth not configured"));
    const host = mount();
    await act(async () => {});
    expect(host.textContent).toContain("No OAuth providers bound");
  });

  it("the password form gates on min length and submits the pair", async () => {
    connectionsMock.mockResolvedValueOnce([]);
    changePwMock.mockResolvedValueOnce(undefined);
    const host = mount();
    await act(async () => {});
    const oldInput = (host.querySelector('[data-testid="password-old"] input')
      ?? host.querySelector('[data-testid="password-old"]')) as HTMLInputElement;
    const newInput = (host.querySelector('[data-testid="password-new"] input')
      ?? host.querySelector('[data-testid="password-new"]')) as HTMLInputElement;
    const submit = (host.querySelector('[data-testid="password-submit"] button')
      ?? host.querySelector('[data-testid="password-submit"]')) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    await act(async () => {
      setType(oldInput, "old-secret");
      setType(newInput, "new-secret-1");
    });
    expect(submit.disabled).toBe(false);
    const form = (host.querySelector('[data-testid="password-form"]')
      ?? submit.closest("form")) as HTMLFormElement;
    await act(async () => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    expect(changePwMock).toHaveBeenCalledWith("old-secret", "new-secret-1");
    expect(host.textContent).toContain("Password changed.");
  });

  it("a rejected password change shows the upstream error inline", async () => {
    connectionsMock.mockResolvedValueOnce([]);
    changePwMock.mockRejectedValueOnce(new Error("old password mismatch"));
    const host = mount();
    await act(async () => {});
    const oldInput = (host.querySelector('[data-testid="password-old"] input')
      ?? host.querySelector('[data-testid="password-old"]')) as HTMLInputElement;
    const newInput = (host.querySelector('[data-testid="password-new"] input')
      ?? host.querySelector('[data-testid="password-new"]')) as HTMLInputElement;
    await act(async () => {
      setType(oldInput, "wrong");
      setType(newInput, "new-secret-1");
    });
    const submit = (host.querySelector('[data-testid="password-submit"] button')
      ?? host.querySelector('[data-testid="password-submit"]')) as HTMLButtonElement;
    const form = (host.querySelector('[data-testid="password-form"]')
      ?? submit.closest("form")) as HTMLFormElement;
    await act(async () => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    expect(host.textContent).toContain("old password mismatch");
    expect(host.querySelector('[data-testid="password-error"]')).toBeTruthy();
  });

  it("a too-short new password is refused even when submitted from the form (enter key path)", async () => {
    connectionsMock.mockResolvedValueOnce([]);
    changePwMock.mockClear();
    const host = mount();
    await act(async () => {});
    const oldInput = (host.querySelector('[data-testid="password-old"] input')
      ?? host.querySelector('[data-testid="password-old"]')) as HTMLInputElement;
    const newInput = (host.querySelector('[data-testid="password-new"] input')
      ?? host.querySelector('[data-testid="password-new"]')) as HTMLInputElement;
    await act(async () => {
      setType(oldInput, "old-secret");
      setType(newInput, "short");
    });
    const submit = (host.querySelector('[data-testid="password-submit"] button')
      ?? host.querySelector('[data-testid="password-submit"]')) as HTMLButtonElement;
    const form = (host.querySelector('[data-testid="password-form"]')
      ?? submit.closest("form")) as HTMLFormElement;
    await act(async () => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    expect(changePwMock).not.toHaveBeenCalled();
  });

  it("unbind refreshes the connections list", async () => {
    connectionsMock.mockResolvedValueOnce([
      { provider: "google", email: "w@example.com" },
    ]);
    unbindMock.mockResolvedValueOnce(undefined);
    connectionsMock.mockResolvedValueOnce([]);
    const host = mount();
    await act(async () => {});
    const btn = (host.querySelector('[data-testid="unbind-google"] button')
      ?? host.querySelector('[data-testid="unbind-google"]')) as HTMLButtonElement;
    await act(async () => {
      btn.click();
    });
    expect(unbindMock).toHaveBeenCalledWith("google");
    expect(host.textContent).toContain("No OAuth providers bound");
  });
});
