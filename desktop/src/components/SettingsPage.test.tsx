import { renderToString } from "react-dom/server";
import { MantineProvider } from "@mantine/core";
import { describe, expect, it, vi } from "vitest";

// The SettingsPage account card (user-facing gap Wesley flagged: "the app's
// Settings features all seem unimplemented"). What exists today is real but
// read-only (model routing, doctor, runtime start/stop); the account card
// adds the sign-in surface the #54 notice used to point at. These pins are
// markup-level (mutation-verified during authoring): each assertion fails if
// its block is deleted.
vi.mock("../api", () => ({
  api: vi.fn(),
}));
// #99 billing card: the ledger fetchers are mocked — the pins are about the
// CARD (structure + states), not the network (beehiveClient has its own file).
vi.mock("../beehiveClient", () => ({
  getBillingHistory: vi.fn().mockResolvedValue({
    events: [
      { event_id: 1, event_type: "settle", amount: -12500, job_id: "job-9", created_at: "2026-10-13T10:00:00Z" },
      { event_id: 2, event_type: "topup", amount: 2_000_000, created_at: "2026-10-12T09:00:00Z" },
    ],
    total: 2,
  }),
  getWalletBalance: vi.fn().mockResolvedValue(1.875),
}));

import SettingsPage from "./SettingsPage";
import type { AuthStatus } from "./LoginScreen";

const baseAuth: AuthStatus = {
  state: "authenticated",
  authenticated: true,
  account: { uid: "1782887282002", name: "Wesley" },
  scopes: ["workflows:read", "workflows:write", "skills:read"],
  expires_in: 30 * 86400,
};

function view(auth: AuthStatus | null) {
  return renderToString(
    <MantineProvider>
      <SettingsPage
        info={{ dry_run: true, port: 8765, pid: 1 } as never}
        model=""
        onModelChange={() => undefined}
        onStopRuntime={() => undefined}
        onStartRuntime={() => undefined}
        connecting={false}
        authStatus={auth}
        onSignOut={() => undefined}
        onRefreshCredential={() => undefined}
      />
    </MantineProvider>,
  );
}

describe("SettingsPage account card", () => {
  it("shows the signed-in account with scopes and expiry", () => {
    const html = view(baseAuth);
    expect(html).toContain("Wesley");
    expect(html).toContain("1782887282002");
    expect(html).toContain("workflows:read");
    expect(html).toContain("skills:read");
    // "30" + " days" split by the SSR separator comment (the G5 lesson):
    expect(/30(\s|<!-- -->)*days/.test(html)).toBe(true);
    expect(html).toContain("settings-sign-out");
    expect(html).toContain("refresh-credential");
  });

  it("shows the pre-scope gap in the scope list (the #54 diagnostic)", () => {
    const stale = { ...baseAuth, scopes: ["workflows:read"] } as AuthStatus;
    const html = view(stale);
    expect(html).toContain("workflows:read");
    expect(html).not.toContain("skills:read");
  });

  it("unauthenticated state shows the dev-mode / not-signed-in badge, no actions", () => {
    const html = view({ state: "unauthenticated", authenticated: false });
    expect(html).toContain("not signed in");
    expect(html).not.toContain("settings-sign-out");
  });

  it("dev mode explains itself instead of offering sign-out", () => {
    const html = view({ state: "unauthenticated", authenticated: false, dev_mode: true });
    expect(html).toContain("dev mode");
    expect(html).toContain("dev mode (no account)");
  });

  it("the dry-run badge explains how to leave dry-run (no silent toggle)", () => {
    const html = view(baseAuth);
    expect(html).toContain("dry run");
    expect(html).toContain("--live");
  });

  // #99: the billing card exists in the settings surface (structure pin —
  // the data path is a happy-dom concern below).
  it("renders the billing card section with a refresh action", () => {
    const html = view(baseAuth);
    expect(html).toContain("Billing");
    expect(html).toContain("billing-refresh");
    expect(html).toContain("Loading billing");
  });

  it("unauthenticated stays out of the billing fetch (no dev-mode noise)", () => {
    // authStatus null -> the loader effect never fires; the card shows the
    // loading state (never an error) — dev mode is not a billing failure.
    const html = view({ state: "unauthenticated", authenticated: false });
    expect(html).toContain("Loading billing");
    expect(html).not.toContain("Could not load the billing ledger");
  });
});
