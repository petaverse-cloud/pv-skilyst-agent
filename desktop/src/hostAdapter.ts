/**
 * Desktop HostAdapter (A3 S1) — the skilyst-studio package's view of the
 * desktop shell. S1 scope is the READ-ONLY canvas; the adapter therefore
 * implements the minimum viable host:
 *
 *  - apiBaseUrl: "" (relative) — dev traffic rides the vite same-origin proxy
 *    (/api → beehive-api.verse4.pet, REST + WS). Production wiring (a core
 *    CORS entry for the desktop origin) is tracked on pv-skilyst-agent#10.
 *  - getToken: the beehive JWT kept in localStorage by the canvas login form
 *    (canvas-local; the runtime's AK/SK credentials are NOT reused here —
 *    they are scope-restricted agent credentials, not user sessions).
 *  - t: key passthrough (the desktop has no i18n yet — package keys are the
 *    English source strings' fallbacks).
 *  - confirm: window.confirm (WebView supports it).
 *  - notify: console for now — the desktop's toast layer lands with the full
 *    workbench integration (S3/S4).
 */

import type { HostAdapter } from "@petaverse/skilyst-studio/host";

export const BEEHIVE_TOKEN_KEY = "skilyst.beehive_token";

export function desktopHostAdapter(): HostAdapter {
  const listeners = new Map<string, Set<(detail?: unknown) => void>>();
  return {
    apiBaseUrl: "",
    getToken: () => window.localStorage.getItem(BEEHIVE_TOKEN_KEY),
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
