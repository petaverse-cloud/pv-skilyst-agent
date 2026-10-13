/**
 * #82: run-completion notifications + dock badge.
 *
 * Design notes (the loud-failure rule applies here too):
 * - A denied notification permission is NOT an error — it degrades to
 *   badge-only, and the degradation is visible (the permission state is
 *   reported back so a Settings row can show it later).
 * - The dock badge count lives in this module (how many runs finished
 *   unseen); the shell owns only the AppKit surface (set_dock_badge).
 * - Window focus clears the count: "unseen" means "the window was not
 *   focused when the run ended".
 */
import { invoke } from "@tauri-apps/api/core";
import {
  isPermissionGranted,
  requestPermission,
  sendNotification,
} from "@tauri-apps/plugin-notification";

let unseenCount = 0;

/** Visible permission state for diagnostics (never a silent unknown). */
let permissionState: "granted" | "denied" | "unknown" = "unknown";

export function notificationPermission(): "granted" | "denied" | "unknown" {
  return permissionState;
}

async function ensurePermission(): Promise<boolean> {
  try {
    if (await isPermissionGranted()) {
      permissionState = "granted";
      return true;
    }
    const asked = await requestPermission();
    permissionState = asked === "granted" ? "granted" : "denied";
    return asked === "granted";
  } catch (error) {
    // The plugin itself failing is loud in the console but never blocks
    // the run flow — the badge still works below.
    console.error("[notifications] permission check failed:", error);
    permissionState = "unknown";
    return false;
  }
}

async function setBadge(count: number): Promise<void> {
  try {
    await invoke("set_dock_badge", { count: count > 0 ? count : null });
  } catch (error) {
    console.error("[notifications] set_dock_badge failed:", error);
  }
}

/**
 * Fire on run end — BOTH outcomes notify (a failed run deserves attention
 * more than a successful one), then bump the badge unless focused.
 */
export async function notifyRunEnd(input: {
  ok: boolean;
  title?: string;
  body: string;
  focused: boolean;
}): Promise<void> {
  if (!input.focused) {
    unseenCount += 1;
    await setBadge(unseenCount);
  }
  const allowed = await ensurePermission();
  if (!allowed) return; // badge-only degradation, by design
  try {
    sendNotification({
      title: input.title ?? (input.ok ? "Run completed" : "Run failed"),
      body: input.body,
    });
  } catch (error) {
    console.error("[notifications] sendNotification failed:", error);
  }
}

/**
 * Clear the badge on focus. Called from the app's focus listener; safe to
 * call repeatedly.
 */
export async function clearUnseenRuns(): Promise<void> {
  if (unseenCount === 0) return;
  unseenCount = 0;
  await setBadge(0);
}

/** Test hook: reset the module state between cases. */
export function __resetForTest(): void {
  unseenCount = 0;
  permissionState = "unknown";
}

export function unseenRunCount(): number {
  return unseenCount;
}
