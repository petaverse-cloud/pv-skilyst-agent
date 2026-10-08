/**
 * A tiny hash router for the desktop shell (issue #31).
 *
 * The desktop is a single-page webview; there is no need for a router
 * package (G1-3: runtime deps must be argued for). The shell needs exactly
 * three things:
 *
 *  - module navigation: #/home, #/workbench, #/settings
 *  - a deep link into a session: #/workbench?session=<id> — keeps the
 *    existing `?session=` deep-link contract alive (Rust listens for the
 *    skilyst:// scheme and redirects the webview with that param).
 *  - surviving a window reload (G3-14: state must not be lost).
 *
 * History API is NOT used: hash routing works from file:// (production
 * build) without server rewrite rules, and location.hash survives reloads
 * for free. `?session=` URLs from the deep-link path are normalized into
 * `#/workbench?session=…` on boot so both entry shapes land on one route.
 */

export type RouteModule = "home" | "workbench" | "settings";

export type Route =
  | { module: "home" }
  | { module: "workbench"; session?: string; workflow?: string }
  | { module: "settings" };

const KNOWN: RouteModule[] = ["home", "workbench", "settings"];

export function parseRoute(hash: string, search: string = ""): Route {
  // The deep-link contract pre-#31: plain `?session=<id>` in the query. Keep
  // accepting it so skilyst:// callbacks keep working unchanged.
  const legacy = new URLSearchParams(search).get("session");
  const body = hash.replace(/^#/, "");
  const [path, query] = body.split("?");
  const params = new URLSearchParams(query ?? "");
  if (legacy) params.set("session", legacy);
  const [head] = path.split("/");
  const module = KNOWN.includes(head as RouteModule) ? (head as RouteModule) : "home";
  if (module === "workbench") {
    const session = params.get("session") ?? undefined;
    const workflow = params.get("workflow") ?? undefined;
    return session || workflow
      ? { module: "workbench", session, workflow }
      : { module: "workbench" };
  }
  return { module };
}

export function currentRoute(): Route {
  return parseRoute(window.location.hash, window.location.search);
}

export function href(route: Route): string {
  switch (route.module) {
    case "home":
      return "#/home";
    case "settings":
      return "#/settings";
    case "workbench": {
      const params = new URLSearchParams();
      if (route.session) params.set("session", route.session);
      if (route.workflow) params.set("workflow", route.workflow);
      const query = params.toString();
      return `#/workbench${query ? `?${query}` : ""}`;
    }
  }
}

export function navigate(route: Route): void {
  window.location.hash = href(route).slice(1);
}

/** Re-anchor a legacy `?session=` URL so the hash router owns the address. */
export function normalizeLegacySessionUrl(): void {
  const legacy = new URLSearchParams(window.location.search).get("session");
  if (!legacy) return;
  const route = currentRoute();
  if (route.module === "workbench" && route.session === legacy) return;
  navigate({ module: "workbench", session: legacy });
  // Strip the query so a reload does not re-enter the deep-link open.
  window.history.replaceState(null, "", window.location.pathname + window.location.hash);
}

/** Subscribe to route changes; returns an unsubscribe. */
export function onRouteChange(handler: (route: Route) => void): () => void {
  const listener = () => handler(currentRoute());
  window.addEventListener("hashchange", listener);
  return () => window.removeEventListener("hashchange", listener);
}
