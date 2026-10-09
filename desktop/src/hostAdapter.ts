/**
 * Desktop HostAdapter (A3 S1) — the skilyst-studio package's view of the
 * desktop shell. Unified server-data posture (#36):
 *
 *  - apiBaseUrl: the local runtime origin. The package's internal apiFetch
 *    therefore hits the runtime proxy, which signs beehive calls with the
 *    keychain AK/SK. No webview→beehive direct call, no localStorage JWT —
 *    the retired canvas login form's token never comes back.
 *  - getToken: the RUNTIME bearer (127.0.0.1 capability token), not a
 *    beehive web session. Retrieved from the same place api.ts gets it.
 *  - t: key passthrough (the desktop has no i18n yet — package keys are the
 *    English source strings' fallbacks).
 *  - confirm: window.confirm (WebView supports it).
 *  - notify: console for now — the desktop's toast layer lands with the full
 *    workbench integration (S3/S4).
 */

import type { HostAdapter } from "@petaverse/skilyst-studio/host";
import { runtime } from "./api";

export function desktopHostAdapter(): HostAdapter {
  const listeners = new Map<string, Set<(detail?: unknown) => void>>();
  const info = runtime();
  return {
    // #36: the runtime origin — the package's internal apiFetch routes
    // "/api/v1/..." through the runtime proxy, keychain-signed. Identical in
    // dev and the installed bundle; no webview→beehive direct calls remain.
    apiBaseUrl: info?.base_url ?? "",
    getToken: () => runtime()?.token ?? null,
    navigate: () => {
      /* the desktop has no router; the canvas Back button is hidden by the
         host view chrome (CanvasView renders its own header). */
    },
    confirm: (message) => window.confirm(message),
    t: (key, optsOrDefault) => {
      if (typeof optsOrDefault === "string") return optsOrDefault;
      return key;
    },
    notify: (_kind, message, description) => {
      // S1: surface in the console; the toast layer comes with S3/S4.
      // eslint-disable-next-line no-console
      console.info(`[skilyst-studio] ${message}${description ? ` — ${description}` : ""}`);
    },
    on: (event, handler) => {
      const set = listeners.get(event) ?? new Set();
      set.add(handler);
      listeners.set(event, set);
      return () => set.delete(handler);
    },
    emit: (event, detail) => {
      for (const h of listeners.get(event) ?? []) h(detail);
    },
    storage: {
      get: (key) => window.localStorage.getItem(key),
      set: (key, value) => window.localStorage.setItem(key, value),
      remove: (key) => window.localStorage.removeItem(key),
    },
  };
}
