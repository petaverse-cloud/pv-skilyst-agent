// @vitest-environment happy-dom
// #99: the billing card's data path — the ledger fetchers feed the card,
// amounts render as micro-USD -> USD, the empty ledger explains itself,
// and a failed ledger is a loud alert (never a silent gap).
import { describe, expect, it, vi, beforeEach } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import React, { act, createElement } from "react";
import { MantineProvider } from "@mantine/core";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
if (typeof globalThis.ResizeObserver === "undefined") {
  class ResizeObserverStub { observe(): void {} unobserve(): void {} disconnect(): void {} }
  globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
}

const historyMock = vi.hoisted(() => vi.fn());
const walletMock = vi.hoisted(() => vi.fn());
// api() must resolve (not reject, not undefined): the component sets
// state from what it resolves — undefined would crash doctor.runnable.
vi.mock("../api", () => ({ api: vi.fn().mockResolvedValue(null) }));
vi.mock("../beehiveClient", () => ({
  getBillingHistory: historyMock,
  getWalletBalance: walletMock,
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

beforeEach(() => {
  historyMock.mockReset();
  walletMock.mockReset();
});

describe("SettingsPage billing card (#99)", () => {
  it("renders ledger events with micro-USD formatting and the wallet balance", async () => {
    historyMock.mockResolvedValueOnce({
      events: [
        { event_id: 1, event_type: "settle", amount: -12500, job_id: "job-9", created_at: "2026-10-13T10:00:00Z" },
        { event_id: 2, event_type: "topup", amount: 2_000_000, created_at: "2026-10-12T09:00:00Z" },
      ],
      total: 2,
    });
    walletMock.mockResolvedValueOnce(1.875);
    const host = mount();
    await act(async () => {});
    expect(historyMock).toHaveBeenCalledWith(20, 0);
    expect(host.textContent).toContain("$1.88");
    // settle -12500 micro-USD -> "-$0.0125"; topup 2_000_000 -> "+$2.0000"
    expect(host.textContent).toContain("-$0.0125");
    expect(host.textContent).toContain("+$2.0000");
    expect(host.textContent).toContain("job-9");
  });

  it("an empty ledger explains itself, no fake zero rows", async () => {
    historyMock.mockResolvedValueOnce({ events: [], total: 0 });
    walletMock.mockResolvedValueOnce(0);
    const host = mount();
    await act(async () => {});
    expect(host.textContent).toContain("No billing events yet");
  });

  it("a failed ledger is a loud alert carrying the upstream error", async () => {
    historyMock.mockRejectedValueOnce(new Error("GET /api/v1/billing/history failed: HTTP 403 scope billing:read not granted"));
    walletMock.mockResolvedValueOnce(null);
    const host = mount();
    await act(async () => {});
    expect(host.textContent).toContain("Could not load the billing ledger");
    expect(host.textContent).toContain("billing:read");
  });

  it("the core zero-time sentinel renders as a dash, not year 0001", async () => {
    historyMock.mockResolvedValueOnce({
      events: [{ event_id: 3, event_type: "reserve", amount: -500, created_at: "0001-01-01T00:00:00Z" }],
      total: 1,
    });
    walletMock.mockResolvedValueOnce(null);
    const host = mount();
    await act(async () => {});
    expect(host.textContent).toContain("—");
    expect(host.textContent).not.toContain("0001");
  });
});
