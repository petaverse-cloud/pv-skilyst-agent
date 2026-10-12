import { describe, expect, it, vi, beforeEach } from "vitest";

// #82: the run-notification module. The unit under test is pure logic —
// when to bump, when to clear, what a denied permission degrades to.
// The AppKit surface (set_dock_badge) and the OS notification itself are
// shell/platform territory and are covered by the installed-bundle probe.
//
// Tauri APIs are mocked at the module boundary: the bridge calls are
// captured, never executed.
const invokeMock = vi.hoisted(() => vi.fn());
vi.mock("@tauri-apps/api/core", () => ({ invoke: invokeMock }));
const notifMock = vi.hoisted(() => ({
  isPermissionGranted: vi.fn(),
  requestPermission: vi.fn(),
  sendNotification: vi.fn(),
}));
vi.mock("@tauri-apps/plugin-notification", () => notifMock);

import {
  notifyRunEnd,
  clearUnseenRuns,
  unseenRunCount,
  notificationPermission,
  __resetForTest,
} from "./notifications";

beforeEach(() => {
  invokeMock.mockReset();
  notifMock.isPermissionGranted.mockReset();
  notifMock.requestPermission.mockReset();
  notifMock.sendNotification.mockReset();
  __resetForTest();
});

describe("notifyRunEnd (#82)", () => {
  it("an unfocused successful run bumps the badge and sends the notification", async () => {
    notifMock.isPermissionGranted.mockResolvedValueOnce(true);
    await notifyRunEnd({ ok: true, body: "make a 3-shot sequence", focused: false });
    // badge bumped to 1
    expect(invokeMock).toHaveBeenCalledWith("set_dock_badge", { count: 1 });
    expect(notifMock.sendNotification).toHaveBeenCalledWith(
      expect.objectContaining({ title: "Run completed", body: "make a 3-shot sequence" }),
    );
    expect(unseenRunCount()).toBe(1);
  });

  it("a failed run notifies too (attention matters MORE on failure)", async () => {
    notifMock.isPermissionGranted.mockResolvedValueOnce(true);
    await notifyRunEnd({ ok: false, body: "BudgetExceeded: refused", focused: false });
    expect(notifMock.sendNotification).toHaveBeenCalledWith(
      expect.objectContaining({ title: "Run failed", body: "BudgetExceeded: refused" }),
    );
    expect(unseenRunCount()).toBe(1);
  });

  it("a focused run does NOT touch the badge (the user is watching)", async () => {
    notifMock.isPermissionGranted.mockResolvedValueOnce(true);
    await notifyRunEnd({ ok: true, body: "x", focused: true });
    expect(invokeMock).not.toHaveBeenCalled();
    expect(unseenRunCount()).toBe(0);
  });

  it("denied permission degrades to badge-only — no throw, state visible", async () => {
    notifMock.isPermissionGranted.mockResolvedValueOnce(false);
    notifMock.requestPermission.mockResolvedValueOnce("denied");
    await notifyRunEnd({ ok: true, body: "x", focused: false });
    expect(invokeMock).toHaveBeenCalledWith("set_dock_badge", { count: 1 });
    expect(notifMock.sendNotification).not.toHaveBeenCalled();
    expect(notificationPermission()).toBe("denied");
    // the badge still worked — the degradation lost the toast, not the signal
    expect(unseenRunCount()).toBe(1);
  });

  it("the badge counts multiple unseen runs", async () => {
    notifMock.isPermissionGranted.mockResolvedValueOnce(true);
    await notifyRunEnd({ ok: true, body: "a", focused: false });
    await notifyRunEnd({ ok: false, body: "b", focused: false });
    expect(invokeMock).toHaveBeenLastCalledWith("set_dock_badge", { count: 2 });
  });
});

describe("clearUnseenRuns (#82)", () => {
  it("focus clears the badge to zero", async () => {
    notifMock.isPermissionGranted.mockResolvedValueOnce(true);
    await notifyRunEnd({ ok: true, body: "a", focused: false });
    await clearUnseenRuns();
    // the shell contract: 0 -> None (remove the badge), not a literal 0 dot
    expect(invokeMock).toHaveBeenLastCalledWith("set_dock_badge", { count: null });
    expect(unseenRunCount()).toBe(0);
  });

  it("clearing with nothing pending is a no-op (no spurious AppKit calls)", async () => {
    await clearUnseenRuns();
    expect(invokeMock).not.toHaveBeenCalled();
  });

  it("a run ending after focus starts a fresh count", async () => {
    notifMock.isPermissionGranted.mockResolvedValueOnce(true);
    await notifyRunEnd({ ok: true, body: "a", focused: false });
    await clearUnseenRuns();
    await notifyRunEnd({ ok: true, body: "b", focused: false });
    expect(invokeMock).toHaveBeenLastCalledWith("set_dock_badge", { count: 1 });
  });
});
